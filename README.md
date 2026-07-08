# PVE-VDI-WEB
PVE服务器上创建VDI桌面主机的自动化部署管理平台，后端自动对接Guacamole远程桌面网关，实现集中管理
VDI 云桌面平台 —— 完整系统架构

系统安装教程：https://www.bilibili.com/video/BV1CaM36bEG5
系统功能介绍演示：https://www.bilibili.com/video/BV1svTX6yEGn/?share_source=copy_web&vd_source=a896bbdb2a1fe4d445abf5537de01aac

声明：所用到的部分开源软件，未作任何修改，仅作环境集成

同时也感谢大家支持，如有不完善以及问题请大家指出，谢谢！


                            ┌─────────────┐
                            │   用户浏览器  │
                            └──────┬──────┘
                                   │ HTTP (80)
                                   ▼
                         ┌─────────────────┐
                         │  Nginx 反向代理  │
                         │  (端口 80)      │
                         └────────┬────────┘
                         /guacamole/  │  /
                       ┌──────────────┴──────────┐
                       ▼                          ▼
            ┌──────────────────┐      ┌──────────────────┐
            │  Guacamole 容器   │      │  Flask 管理面板    │
            │  (Tomcat, 8080)   │      │  (Python, 5000)    │
            │  + guacd (4822)   │      └────────┬─────────┘
            └────────┬─────────┘               │
                     │                         │
                     │ 远程桌面协议              │  API 调用
                     ▼                         ▼
            ┌──────────────────┐      ┌──────────────────┐
            │  虚拟桌面 (VM)     │      │  Proxmox VE 主机   │
            │  (Windows/Linux)  │      │  (API 8006)        │
            └──────────────────┘      └─────────┬─────────┘
                                                │ 管理虚拟机
                                                ▼
                              ┌─────────────────────────────┐
                              │  Proxmox 主机 (虚拟化平台)     │
                              │  模板克隆、快照、开机/关机      │
                              └─────────────────────────────┘

![image](https://github.com/geng-shao/PVE-VDI-WEB/blob/main/gongnengtu.png)

核心交互流
管理员/用户 通过浏览器访问 Nginx 80 端口。

Nginx 将 / 路径代理到 Flask (5000)，/guacamole/ 代理到 Guacamole (8080)。

Flask 管理面板 通过 Proxmox API (8006) 管理虚拟机，通过 MySQL (3306) 存储元数据。

Guacamole 通过 guacd 代理 RDP/SSH 等协议，连接虚拟机。

定时任务 由 APScheduler 驱动，每分钟检查数据库并执行到期任务。

数据流向
用户创建桌面 → Flask 调用 Proxmox 克隆 VM → 写入 Guacamole 连接 → 分配用户权限。

用户通过 Guacamole 打开桌面 → Guacamole 从 MySQL 验证用户 → 读取连接参数 → guacd 建立远程连接。

定时任务执行 → Flask 后台检查数据库 → 调用 Proxmox 执行操作 → 更新任务状态。

此架构保证了高内聚、低耦合，所有组件均可独立替换或扩展。

