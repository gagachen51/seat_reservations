"""展示监听地址；不依赖外网，也不自动修改系统防火墙。"""

from __future__ import annotations

import ipaddress
import socket


def local_ipv4_addresses() -> list[str]:
    """返回候选地址。VPN/虚拟网卡可能在列，不能保证远端可达。"""
    try:
        records = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return []
    addresses = set()
    for record in records:
        address = ipaddress.IPv4Address(record[4][0])
        if not (address.is_loopback or address.is_unspecified or address.is_link_local
                or address.is_multicast):
            addresses.add(str(address))
    return sorted(addresses, key=ipaddress.IPv4Address)


def print_access_addresses(host: str, port: int) -> None:
    print("服务启动后可访问以下地址（请等待 Uvicorn 启动成功）：", flush=True)
    if host == "0.0.0.0":
        print(f"本机：http://127.0.0.1:{port}/")
        for address in local_ipv4_addresses():
            print(f"局域网候选：http://{address}:{port}/")
        print("其他设备请选择主机 Wi-Fi/以太网 IPv4 地址；可运行 ipconfig 查看。")
        print("主机与客户端须在同一局域网，并允许该端口通过专用网络防火墙。")
        print("0.0.0.0 是监听地址，不能作为其他设备的访问地址。", flush=True)
    else:
        url_host = f"[{host}]" if ":" in host else host
        print(f"访问：http://{url_host}:{port}/", flush=True)
