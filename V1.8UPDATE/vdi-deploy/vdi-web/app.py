#!/usr/bin/env python3
"""VDI 云桌面管理平台（完整版，含 update/schedule）"""
import os, time, hashlib, uuid, logging, secrets, csv, io
from functools import wraps
from datetime import datetime, timezone, timedelta
import mysql.connector
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from proxmoxer import ProxmoxAPI
from apscheduler.schedulers.background import BackgroundScheduler
import yaml

def load_config():
    path = os.environ.get("VDI_CONFIG", "/opt/vdi-deploy/vdi-web/config.yaml")
    with open(path) as f: cfg = yaml.safe_load(f)
    if cfg.get("guacamole",{}).get("port") is None: cfg["guacamole"]["port"]=""
    return cfg

CONFIG=load_config()
app=Flask(__name__)
app.secret_key=CONFIG["web"]["secret_key"]
logging.basicConfig(level=logging.INFO)

def format_bytes(b):
    if not b or b==0: return "0 B"
    u=["B","KB","MB","GB","TB"]; i,s=0,float(b)
    while s>=1024 and i<len(u)-1: s/=1024; i+=1
    return f"{s:.1f} {u[i]}"

def get_db(): return mysql.connector.connect(**CONFIG["database"])

class PM:
    def __init__(self):
        p=CONFIG["proxmox"]; self.n=p["node"]
        self.a=ProxmoxAPI(p["host"],user=p["user"],token_name=p["token_name"],token_value=p["token_value"],verify_ssl=p["verify_ssl"])
    def _wait(self,u):
        na=self.a.nodes(self.n)
        while True:
            s=na.tasks(u).status.get()
            if s["status"]=="stopped": return s["exitstatus"]=="OK"
            time.sleep(2)
    def get_templates(self):
        return [{"vmid":v["vmid"],"name":v.get("name","")} for v in self.a.nodes(self.n).qemu.get() if v.get("template")==1]
    def get_storages(self):
        r=[]
        for s in self.a.nodes(self.n).storage.get():
            if s.get("content") and "images" in s["content"].split(","):
                t,u,a=s.get("total",0),s.get("used",0),s.get("avail",0)
                r.append({"storage":s["storage"],"type":s.get("type",""),"total":t,"used":u,"avail":a,"percent":round(u/max(t,1)*100,1),"total_str":format_bytes(t),"used_str":format_bytes(u),"avail_str":format_bytes(a)})
        return r
    def get_node_status(self):
        try:
            s=self.a.nodes(self.n).status.get(); m=s.get("memory",{})
            return {"cpu":round(s.get("cpu",0)*100,1),"cpu_cores":s.get("cpuinfo",{}).get("cpus",0),"memory_total":m.get("total",0),"memory_used":m.get("used",0),"memory_percent":round(m.get("used",0)/max(m.get("total",1),1)*100,1),"memory_total_str":format_bytes(m.get("total",0)),"memory_used_str":format_bytes(m.get("used",0))}
        except: return {"cpu":0,"memory_percent":0}
    def get_vm_status(self,i):
        try: return self.a.nodes(self.n).qemu(i).status.current.get().get("status","unknown")
        except: return "unknown"
    def clone_vm(self,t,i,n,s=None,f=0):
        na=self.a.nodes(self.n); p={"newid":i,"name":n,"full":f,"target":self.n}
        if f==1 and s: p["storage"]=s
        if not self._wait(na.qemu(t).clone.post(**p)): raise Exception("克隆失败")
        na.qemu(i).status.start.post()
    def get_vm_ip(self,i,to=120):
        na=self.a.nodes(self.n); st=time.time()
        while time.time()-st<to:
            try:
                for iface in na.qemu(i).agent("network-get-interfaces").get().get("result",[]):
                    if iface.get("name")=="lo": continue
                    for ip in iface.get("ip-addresses",[]):
                        if ip["ip-address-type"]=="ipv4": return ip["ip-address"]
            except: pass
            time.sleep(5)
        raise Exception("获取IP超时")
    def start_vm(self,i): self.a.nodes(self.n).qemu(i).status.start.post()
    def stop_vm(self,i): self.a.nodes(self.n).qemu(i).status.stop.post()
    def destroy_vm(self,i):
        na=self.a.nodes(self.n)
        try: na.qemu(i).status.stop.post(); time.sleep(3)
        except: pass
        na.qemu(i).delete()
    def create_snapshot(self,i,n,d=""): self.a.nodes(self.n).qemu(i).snapshot.post(snapname=n,description=d)
    def list_snapshots(self,i):
        try: return self.a.nodes(self.n).qemu(i).snapshot.get()
        except: return []
    def rollback_snapshot(self,i,n): self.a.nodes(self.n).qemu(i).snapshot(n).rollback.post()
    def delete_snapshot(self,i,n): self.a.nodes(self.n).qemu(i).snapshot(n).delete()

class DB:
    def __init__(self): self.c=get_db()
    def __enter__(self): return self
    def __exit__(self,*a): self.c.close()
    def close(self):
        if self.c: self.c.close()
    def _hash(self,p):
        s=secrets.token_bytes(32).hex().upper(); return s, hashlib.sha256((p+s).encode()).hexdigest().upper()
    def add_user(self,u,p):
        cur=self.c.cursor()
        cur.execute("SELECT entity_id FROM guacamole_entity WHERE name=%s AND type='USER'",(u,))
        r=cur.fetchone(); eid=r[0] if r else None
        if not eid: cur.execute("INSERT INTO guacamole_entity(name,type) VALUES(%s,'USER')",(u,)); eid=cur.lastrowid
        s,h=self._hash(p)
        cur.execute("INSERT INTO guacamole_user(entity_id,password_salt,password_hash,password_date) VALUES(%s,UNHEX(%s),UNHEX(%s),NOW()) ON DUPLICATE KEY UPDATE password_salt=VALUES(password_salt),password_hash=VALUES(password_hash)",(eid,s,h))
        self.c.commit(); return eid
    def add_connection(self,n,ip,vu,vp,vi,sec):
        rdp=CONFIG["rdp_defaults"]; cur=self.c.cursor()
        cur.execute("INSERT INTO guacamole_connection(connection_name,protocol) VALUES(%s,'rdp')",(n,)); cid=cur.lastrowid
        for p,v in [("hostname",ip),("port",rdp["port"]),("username",vu),("password",vp),("security",sec),("vmid",str(vi))]+[(k,v) for k,v in rdp.items() if k not in("port","security")]:
            cur.execute("INSERT INTO guacamole_connection_parameter(connection_id,parameter_name,parameter_value) VALUES(%s,%s,%s)",(cid,p,v))
        self.c.commit(); return cid
    def authorize(self,e,c): self.c.cursor().execute("INSERT INTO guacamole_connection_permission(entity_id,connection_id,permission) VALUES(%s,%s,'READ')",(e,c)); self.c.commit()
    def list_desktops(self):
        cur=self.c.cursor(dictionary=True)
        cur.execute("SELECT c.connection_id,c.connection_name,e.name username,host.parameter_value ip,vmid.parameter_value vmid,vu.parameter_value vm_username,sec.parameter_value security_mode FROM guacamole_connection c LEFT JOIN guacamole_connection_permission p ON c.connection_id=p.connection_id LEFT JOIN guacamole_entity e ON p.entity_id=e.entity_id LEFT JOIN guacamole_connection_parameter host ON c.connection_id=host.connection_id AND host.parameter_name='hostname' LEFT JOIN guacamole_connection_parameter vmid ON c.connection_id=vmid.connection_id AND vmid.parameter_name='vmid' LEFT JOIN guacamole_connection_parameter vu ON c.connection_id=vu.connection_id AND vu.parameter_name='username' LEFT JOIN guacamole_connection_parameter sec ON c.connection_id=sec.connection_id AND sec.parameter_name='security'")
        return cur.fetchall()
    def remove_connection(self,c):
        cur=self.c.cursor()
        for t in["guacamole_connection_parameter","guacamole_connection_permission","guacamole_connection"]: cur.execute(f"DELETE FROM {t} WHERE connection_id=%s",(c,))
        self.c.commit()
    def remove_user(self,e,u):
        cur=self.c.cursor()
        cur.execute("DELETE FROM guacamole_connection_permission WHERE entity_id=%s",(e,)); cur.execute("DELETE FROM guacamole_user WHERE entity_id=%s",(e,)); cur.execute("DELETE FROM guacamole_entity WHERE entity_id=%s AND name=%s",(e,u))
        self.c.commit()
    def get_entity(self,u):
        cur=self.c.cursor(); cur.execute("SELECT entity_id FROM guacamole_entity WHERE name=%s AND type='USER'",(u,)); r=cur.fetchone(); return r[0] if r else None
    def validate_user_password(self,u,p):
        cur=self.c.cursor(dictionary=True)
        cur.execute("SELECT u.password_salt,u.password_hash FROM guacamole_user u JOIN guacamole_entity e ON u.entity_id=e.entity_id WHERE e.name=%s AND e.type='USER'",(u,)); r=cur.fetchone()
        if not r: return False
        return hashlib.sha256((p+r['password_salt'].hex().upper()).encode()).hexdigest().upper()==r['password_hash'].hex().upper()
    def add_schedule(self,c,i,a,t): self.c.cursor().execute("INSERT INTO vdi_schedule(connection_id,vmid,action,execute_at) VALUES(%s,%s,%s,%s)",(c,i,a,t)); self.c.commit()
    def get_pending_schedules(self):
        cur=self.c.cursor(dictionary=True); cur.execute("SELECT * FROM vdi_schedule WHERE executed=0 AND execute_at<=NOW()"); return cur.fetchall()
    def mark_schedule_executed(self,i): self.c.cursor().execute("UPDATE vdi_schedule SET executed=1 WHERE id=%s",(i,)); self.c.commit()
    def update_connection(self, conn_id, ip, vm_username, vm_password, security_mode):
        cur = self.c.cursor()
        for pname, pval in [("hostname", ip), ("username", vm_username), ("security", security_mode)]:
            cur.execute("UPDATE guacamole_connection_parameter SET parameter_value=%s WHERE connection_id=%s AND parameter_name=%s", (pval, conn_id, pname))
        if vm_password:
            cur.execute("UPDATE guacamole_connection_parameter SET parameter_value=%s WHERE connection_id=%s AND parameter_name='password'", (vm_password, conn_id))
        self.c.commit()
    def update_guac_user(self, entity_id, new_username=None, new_password=None):
        cur = self.c.cursor()
        if new_username:
            cur.execute("UPDATE guacamole_entity SET name=%s WHERE entity_id=%s", (new_username, entity_id))
        if new_password:
            s, h = self._hash(new_password)
            cur.execute("UPDATE guacamole_user SET password_salt=UNHEX(%s), password_hash=UNHEX(%s), password_date=NOW() WHERE entity_id=%s", (s, h, entity_id))
        self.c.commit()

def login_required(f):
    @wraps(f)
    def d(*a,**k):
        if not session.get("logged_in"): return redirect(url_for("login"))
        return f(*a,**k)
    return d

scheduler=BackgroundScheduler()
def process():
    db=DB()
    try:
        for t in db.get_pending_schedules() or []:
            try:
                pm=PM()
                if t["action"]=="startup": pm.start_vm(t["vmid"])
                elif t["action"]=="shutdown": pm.stop_vm(t["vmid"])
                elif t["action"]=="destroy": pm.destroy_vm(t["vmid"])
                elif t["action"]=="snapshot": pm.create_snapshot(t["vmid"], f"auto_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
                db.mark_schedule_executed(t["id"])
            except Exception as e:
                logging.error(f"定时任务执行失败: {e}")
    finally: db.close()
scheduler.add_job(process,'interval',minutes=1)
if not scheduler.running: scheduler.start()

@app.route("/")
@login_required
def index():
    pm=PM(); tpl=pm.get_templates(); sto=pm.get_storages(); ns=pm.get_node_status()
    db=DB(); des=db.list_desktops(); db.close()
    rc=sc=0
    for d in des:
        d["status"]=pm.get_vm_status(int(d["vmid"])) if d.get("vmid") else "N/A"
        if d["status"]=="running": rc+=1
        elif d["status"]=="stopped": sc+=1
    return render_template("index.html",templates=tpl,storages=sto,desktops=des,node_status=ns,guac_config=CONFIG["guacamole"],running_count=rc,stopped_count=sc,current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

@app.route("/login",methods=["GET","POST"])
def login():
    e=None
    if request.method=="POST":
        if request.form.get("username","").strip()=="admin" and request.form.get("password","").strip()==CONFIG["web"]["admin_password"]:
            session["logged_in"]=True; return redirect(url_for("index"))
        e="用户名或密码错误"
    return render_template("login.html",error=e)

@app.route("/logout")
def logout(): session.pop("logged_in",None); return redirect(url_for("login"))

@app.route("/add",methods=["POST"])
@login_required
def add():
    gu=request.form["guac_username"].strip(); gp=request.form["guac_password"].strip()
    vu=request.form["vm_username"].strip(); vp=request.form["vm_password"].strip()
    nm=request.form["vmname"].strip(); vi=int(request.form["vmid"]); ti=int(request.form["template"])
    im=request.form.get("ip_mode","auto"); mi=request.form.get("manual_ip","").strip()
    se=request.form.get("security_mode","any"); cm=request.form.get("clone_mode","link")
    fu=1 if cm=="full" else 0; sg=request.form.get("storage","").strip() or None
    pm=PM()
    try: pm.clone_vm(ti,vi,nm,sg,fu)
    except Exception as ex: flash(f"克隆失败:{ex}","danger"); return redirect(url_for("index"))
    ip=mi if im=="manual" and mi else None
    if not ip:
        try: ip=pm.get_vm_ip(vi,180)
        except: flash("获取IP失败","warning"); return redirect(url_for("index"))
    if ip:
        db=DB()
        try: ei=db.add_user(gu,gp); ci=db.add_connection(nm,ip,vu,vp,vi,se); db.authorize(ei,ci); flash(f"桌面 {nm} 创建成功","success")
        except Exception as ex: flash(f"数据库错误:{ex}","danger")
        finally: db.close()
    return redirect(url_for("index"))

@app.route("/batch_add", methods=["POST"])
@login_required
def batch_add():
    f = request.files.get("csv_file")
    if not f:
        flash("请上传CSV文件", "danger")
        return redirect(url_for("index"))
    
    stream = io.StringIO(f.stream.read().decode("UTF-8"))
    reader = csv.DictReader(stream)
    prox = PM()
    sc = 0
    el = []
    db = DB()
    
    for row in reader:
        try:
            gu = row["guac_username"].strip()
            gp = row["guac_password"].strip()
            vu = row["vm_username"].strip()
            vp = row["vm_password"].strip()
            nm = row["vmname"].strip()
            vi = int(row["vmid"])
            ti = int(row["template"])
            cm = row.get("clone_mode", "full").strip()
            fu = 1 if cm == "full" else 0
            
            # IP 获取方式：auto 或 manual
            ip_mode = row.get("ip_mode", "auto").strip()
            manual_ip = row.get("manual_ip", "").strip()
            
            # 克隆虚拟机
            prox.clone_vm(ti, vi, nm, None, fu)
            
            # 获取 IP
            if ip_mode == "manual" and manual_ip:
                ip = manual_ip
            else:
                ip = prox.get_vm_ip(vi, to=180)
            
            if not ip:
                el.append(f"{nm}: IP获取失败")
                continue
            
            sec = row.get("security_mode", "any").strip()
            ei = db.add_user(gu, gp)
            ci = db.add_connection(nm, ip, vu, vp, vi, sec)
            db.authorize(ei, ci)
            db.c.commit()
            sc += 1
        except Exception as e:
            el.append(f"{nm}: {e}")
    
    db.c.commit()
    db.close()
    
    flash(f"批量创建完成，成功: {sc}，失败: {len(el)}", "info" if not el else "warning")
    for err in el[:5]:
        flash(f"  - {err}", "warning")
    return redirect(url_for("index"))

@app.route("/delete", methods=["POST"])
@login_required
def delete():
    nm = request.form["vmname"]
    vmid_str = request.form.get("vmid", "0").strip()
    vmid = int(vmid_str) if vmid_str.isdigit() else 0

    db = DB()
    try:
        desktops = db.list_desktops()
        target = next((d for d in desktops if d["connection_name"] == nm), None)
        if not target:
            flash("未找到桌面", "danger")
            return redirect(url_for("index"))

        # 清理数据库连接和用户
        conn_id = target["connection_id"]
        db.remove_connection(conn_id)
        username = target.get("username")
        if username:
            entity_id = db.get_entity(username)
            if entity_id:
                db.remove_user(entity_id, username)
        flash(f"桌面 {nm} 已删除", "success")
    except Exception as e:
        flash(f"删除失败: {e}", "danger")
    finally:
        db.close()

    # 仅当 VMID 有效（>0 且存在于 Proxmox）时才销毁虚拟机
    if vmid > 0:
        try:
            prox = PM()
            if prox.get_vm_status(vmid):       # 检查虚拟机是否存在
                prox.destroy_vm(vmid)
        except Exception:
            # 虚拟机可能已被手动删除，忽略错误
            pass

    return redirect(url_for("index"))


@app.route("/power",methods=["POST"])
@login_required
def power():
    vi=int(request.form["vmid"]); a=request.form["action"]; pm=PM()
    if a=="start": pm.start_vm(vi)
    elif a=="stop": pm.stop_vm(vi)
    flash(f"虚拟机 {vi} 已{'开机' if a=='start' else '关机'}","success"); return redirect(url_for("index"))

@app.route("/update", methods=["POST"])
@login_required
def update_desktop():
    conn_id = int(request.form["conn_id"])
    ip = request.form["ip"].strip()
    vm_username = request.form["vm_username"].strip()
    vm_password = request.form["vm_password"].strip()
    security_mode = request.form["security_mode"].strip()
    guac_username = request.form.get("guac_username", "").strip()
    guac_password = request.form.get("guac_password", "").strip()
    if not ip or not vm_username:
        flash("IP 和虚拟机用户名不能为空", "danger")
        return redirect(url_for("index"))
    db = DB()
    try:
        db.update_connection(conn_id, ip, vm_username, vm_password, security_mode)
        if guac_username or guac_password:
            cur = db.c.cursor(dictionary=True)
            cur.execute("SELECT e.entity_id, e.name FROM guacamole_connection_permission p JOIN guacamole_entity e ON p.entity_id = e.entity_id WHERE p.connection_id = %s AND e.type = 'USER'", (conn_id,))
            row = cur.fetchone()
            if row:
                db.update_guac_user(row["entity_id"], new_username=guac_username if guac_username != row["name"] else None, new_password=guac_password if guac_password else None)
        flash("连接参数已更新", "success")
    except Exception as e:
        flash(f"更新失败: {e}", "danger")
    finally:
        db.close()
    return redirect(url_for("index"))

@app.route("/snapshot",methods=["POST"])
@login_required
def snapshot():
    vi=int(request.form["vmid"]); a=request.form["action"]; pm=PM()
    if a=="create": pm.create_snapshot(vi,f"snap_{int(time.time())}"); flash("快照已创建","success")
    elif a=="list": return jsonify(pm.list_snapshots(vi))
    elif a=="rollback": pm.rollback_snapshot(vi,request.form["snapname"]); flash("已回滚","success")
    elif a=="delete": pm.delete_snapshot(vi,request.form["snapname"]); flash("快照已删除","success")
    return redirect(url_for("index"))

@app.route("/schedule",methods=["POST"])
@login_required
def schedule():
    nm=request.form["vmname"]; vi=int(request.form["vmid"]); a=request.form["schedule_action"]; el=request.form["execute_at"]
    dt=datetime.fromisoformat(el).replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    db=DB(); ds=db.list_desktops(); t=next((d for d in ds if d["connection_name"]==nm),None)
    if t: db.add_schedule(t["connection_id"],vi,a,dt); db.close(); flash("定时任务已添加","success")
    return redirect(url_for("index"))

@app.route("/api/schedules")
@login_required
def api_schedules():
    c=get_db(); cur=c.cursor(dictionary=True)
    cur.execute("SELECT s.*,c.connection_name FROM vdi_schedule s LEFT JOIN guacamole_connection c ON s.connection_id=c.connection_id ORDER BY s.execute_at DESC")
    r=cur.fetchall(); cur.close(); c.close(); return jsonify(r)

@app.route("/schedule/delete",methods=["POST"])
@login_required
def delete_schedule():
    c=get_db(); cur=c.cursor(); cur.execute("DELETE FROM vdi_schedule WHERE id=%s",(int(request.form["schedule_id"]),)); c.commit(); cur.close(); c.close()
    flash("定时任务已删除","success"); return redirect(url_for("index"))

#VDI 申请审批页面增加request


@app.route("/audit")
@login_required
def audit():
    p=request.args.get("page",1,type=int); pp=20; c=get_db(); cur=c.cursor(dictionary=True)
    cur.execute("SELECT COUNT(*) t FROM vdi_audit_log"); tp=max(1,(cur.fetchone()["t"]+pp-1)//pp)
    cur.execute("SELECT * FROM vdi_audit_log ORDER BY created_at DESC LIMIT %s OFFSET %s",(pp,(p-1)*pp))
    l=cur.fetchall(); cur.close(); c.close()
    return render_template("audit.html",logs=l,page=p,total_pages=tp)
 # 取消点击桌面连接后的二次认证
@app.route("/user", methods=["GET", "POST"])
def user():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "login":
            username = request.form["username"].strip()
            password = request.form["password"].strip()
            db = DB()
            if db.validate_user_password(username, password):
                session["user"] = username
                db.close()
                return redirect(url_for("user"))
            db.close()
            flash("用户名或密码错误", "danger")
            return render_template("user_login.html")
        elif action == "change_password":
            if "user" not in session:
                return redirect(url_for("user"))
            new_password = request.form["new_password"].strip()
            if new_password:
                db = DB()
                entity_id = db.get_entity(session["user"])
                if entity_id:
                    s, h = db._hash(new_password)
                    cur = db.c.cursor()
                    cur.execute(
                        "UPDATE guacamole_user SET password_salt=UNHEX(%s), password_hash=UNHEX(%s), "
                        "password_date=NOW() WHERE entity_id=%s",
                        (s, h, entity_id)
                    )
                    db.c.commit()
                db.close()
                flash("密码修改成功", "success")
            return redirect(url_for("user"))
        elif action == "logout":
            session.pop("user", None)
            return redirect(url_for("user"))

    # GET 请求：显示用户桌面列表
    if "user" not in session:
        return render_template("user_login.html")

    username = session["user"]
    prox = PM()
    db = DB()
    cur = db.c.cursor(dictionary=True)

    # 查询桌面及组名（用于直连令牌）
    cur.execute("""
        SELECT c.connection_id,
               c.connection_name,
               host.parameter_value AS ip,
               vmid.parameter_value AS vmid,
               g.connection_group_name AS group_name
        FROM guacamole_connection c
        JOIN guacamole_connection_permission p ON c.connection_id = p.connection_id
        JOIN guacamole_entity e ON p.entity_id = e.entity_id
        LEFT JOIN guacamole_connection_parameter host
            ON c.connection_id = host.connection_id AND host.parameter_name = 'hostname'
        LEFT JOIN guacamole_connection_parameter vmid
            ON c.connection_id = vmid.connection_id AND vmid.parameter_name = 'vmid'
        LEFT JOIN guacamole_connection_group g
            ON c.parent_id = g.connection_group_id
        WHERE e.name = %s AND e.type = 'USER'
    """, (username,))
    desktops = cur.fetchall()
    cur.close()
    db.close()

    # 获取虚拟机状态（仅对有 VMID 的桌面）
    for d in desktops:
        if d.get("vmid"):
            try:
                d["status"] = prox.get_vm_status(int(d["vmid"]))
            except:
                d["status"] = "unknown"
        else:
            d["status"] = "unknown"

    return render_template(
        "user_desktop.html",
        username=username,
        desktops=desktops,
        guac_config=CONFIG["guacamole"]
    )

# ==================== 批量操作 ====================
@app.route("/batch_power", methods=["POST"])
@login_required
def batch_power():
    vmids = request.form.getlist("vmids[]")
    action = request.form.get("action")
    prox = PM()
    success = 0
    for vmid in vmids:
        try:
            if vmid and vmid != "N/A":
                if action == "start": prox.start_vm(int(vmid))
                elif action == "stop": prox.stop_vm(int(vmid))
                success += 1
        except: pass
    flash(f"批量操作完成，成功: {success}/{len(vmids)}", "success")
    return redirect(url_for("index"))

@app.route("/batch_delete", methods=["POST"])
@login_required
def batch_delete():
    entries = request.form.getlist("entries[]")
    prox = PM()
    db = DB()
    success = 0
    for entry in entries:
        parts = entry.split("|")
        if len(parts) >= 3:
            vmid, conn_name, username = parts[0], parts[1], parts[2]
            try:
                desktops = db.list_desktops()
                target = next((d for d in desktops if d["connection_name"] == conn_name), None)
                if target:
                    db.remove_connection(target["connection_id"])
                    if username:
                        eid = db.get_entity(username)
                        if eid: db.remove_user(eid, username)
                if vmid and vmid != "0": prox.destroy_vm(int(vmid))
                success += 1
            except: pass
    db.close()
    flash(f"批量删除完成，成功: {success}/{len(entries)}", "success")
    return redirect(url_for("index"))

@app.route("/batch_snapshot", methods=["POST"])
@login_required
def batch_snapshot():
    vmids = request.form.getlist("vmids[]")
    prox = PM()
    success = 0
    for vmid in vmids:
        try:
            if vmid and vmid != "N/A":
                prox.create_snapshot(int(vmid), f"batch_{int(time.time())}_{vmid}", "批量快照")
                success += 1
        except: pass
    flash(f"批量快照创建完成，成功: {success}/{len(vmids)}", "success")
    return redirect(url_for("index"))

# ==================== 用户自助申请 ====================
@app.route("/request", methods=["GET", "POST"])
def desktop_request():
    if request.method == "POST":
        applicant = request.form.get("applicant", "").strip()
        template_id = request.form.get("template_id", "").strip()
        vmname = request.form.get("vmname", "").strip()
        reason = request.form.get("reason", "").strip()
        if not applicant or not template_id:
            flash("请填写必填项", "danger")
            return redirect(url_for("desktop_request"))
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO vdi_desktop_request (applicant, template_id, vmname, reason) VALUES (%s,%s,%s,%s)", (applicant, template_id, vmname, reason))
        conn.commit()
        cur.close(); conn.close()
        flash("申请已提交，请等待管理员审批", "success")
        return redirect(url_for("desktop_request"))
    prox = PM()
    templates = prox.get_templates()
    return render_template("request.html", templates=templates)

# ==================== 审批管理（防重复+已修复参数）====================
@app.route("/approve")
@login_required
def approve_list():
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM vdi_desktop_request ORDER BY created_at DESC")
    requests = cur.fetchall()
    cur.close(); conn.close()
    return render_template("approve.html", requests=requests)

@app.route("/approve_action", methods=["POST"])
@login_required
def approve_action():
    req_id = int(request.form["req_id"])
    action = request.form["action"]
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM vdi_desktop_request WHERE id=%s", (req_id,))
    req = cur.fetchone()
    if not req:
        flash("申请不存在", "danger")
        return redirect(url_for("approve_list"))
    if req["status"] != "pending":
        flash("该申请已被处理", "warning")
        return redirect(url_for("approve_list"))
    if action == "approve":
        try:
            prox = PM()
            vmid = int(time.time()) % 10000 + 1000
            prox.clone_vm(req["template_id"], vmid, req["vmname"] or f"desk-{req['applicant']}")
            db = DB()
            import secrets as _secrets
            password = _secrets.token_hex(8)
            eid = db.add_user(req["applicant"], password)
            ip = prox.get_vm_ip(vmid, 180)
            cid = db.add_connection(req["vmname"] or f"desk-{req['applicant']}", ip, "administrator", "", vmid, "any")
            db.authorize(eid, cid)
            db.close()
            cur.execute("UPDATE vdi_desktop_request SET status='approved', reviewer='admin', vmid=%s, reviewed_at=NOW() WHERE id=%s", (vmid, req_id))
            conn.commit()
            flash(f"已批准并创建桌面，用户 {req['applicant']} 的密码为 {password}", "success")
        except Exception as e:
            flash(f"创建桌面失败: {e}", "danger")
    else:
        cur.execute("UPDATE vdi_desktop_request SET status='rejected', reviewer='admin', reviewed_at=NOW() WHERE id=%s", (req_id,))
        conn.commit()
        flash("已拒绝该申请", "info")
    cur.close(); conn.close()
    return redirect(url_for("approve_list"))

# ==================== 创建已有主机物理主机连接 ====================

@app.route("/add_existing", methods=["POST"])
@login_required
def add_existing():
    guac_username = request.form["guac_username"].strip()
    guac_password = request.form["guac_password"].strip()
    vm_username = request.form["vm_username"].strip()
    vm_password = request.form["vm_password"].strip()
    vmname = request.form["vmname"].strip()
    ip = request.form["ip"].strip()
    vmid = request.form.get("vmid", "").strip()  # 可选
    security_mode = request.form.get("security_mode", "any").strip()

    if not ip or not vmname:
        flash("IP 和连接名称不能为空", "danger")
        return redirect(url_for("index"))

    db = DB()
    try:
        # 创建 Guacamole 用户和连接
        eid = db.add_user(guac_username, guac_password)
        # 添加 vmid 参数（如果有），用于后续识别 PVE 主机
        cid = db.add_connection(vmname, ip, vm_username, vm_password, vmid, security_mode)
        db.authorize(eid, cid)
        flash(f"已添加主机 {vmname} (IP: {ip})", "success")
    except Exception as e:
        flash(f"添加失败: {e}", "danger")
    finally:
        db.close()
    return redirect(url_for("index"))

# ==================== 修改密码路由 ====================

@app.route("/change_password", methods=["POST"])
@login_required
def change_admin_password():
    new_password = request.form.get("new_password", "").strip()
    if new_password:
        # 更新 config.yaml 中的管理员密码
        import yaml
        config_path = os.environ.get("VDI_CONFIG", "/opt/vdi-deploy/vdi-web/config.yaml")
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        config["web"]["admin_password"] = new_password
        with open(config_path, 'w') as f:
            yaml.dump(config, f)
        # 同时更新 CONFIG 变量
        CONFIG["web"]["admin_password"] = new_password
        flash("密码修改成功，下次登录请使用新密码", "success")
    return redirect(url_for("index"))


@app.route("/stats")
@login_required
def usage_stats():
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT COUNT(*) as total FROM vdi_desktop_usage WHERE action='connect'")
    total_connects = cur.fetchone()["total"]
    cur.execute("SELECT u.connection_id, c.connection_name, COUNT(*) as count FROM vdi_desktop_usage u LEFT JOIN guacamole_connection c ON u.connection_id=c.connection_id WHERE u.action='connect' GROUP BY u.connection_id ORDER BY count DESC LIMIT 10")
    top_desktops = cur.fetchall()
    cur.execute("SELECT DATE(created_at) as date, COUNT(DISTINCT username) as users FROM vdi_desktop_usage WHERE action='connect' AND created_at>=DATE_SUB(NOW(),INTERVAL 30 DAY) GROUP BY DATE(created_at) ORDER BY date")
    daily_users = cur.fetchall()
    cur.close(); conn.close()
    return render_template("stats.html", total_connects=total_connects, top_desktops=top_desktops, daily_users=daily_users)

if __name__=="__main__":
    if not scheduler.running: scheduler.start()
    app.run(host=CONFIG["web"]["listen_host"],port=CONFIG["web"]["listen_port"])

# ==================== 使用统计 ====================
