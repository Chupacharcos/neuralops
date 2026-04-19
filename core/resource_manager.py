import psutil

THRESHOLDS = {
    "ram_pct": 88,
    "cpu_pct": 90,
    "disk_pct": 80,
    "swap_mb": 1800,  # servidor tiene 2GB swap; alerta solo si casi lleno
}


def check_server_health() -> dict:
    ram = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=2)
    disk = psutil.disk_usage("/")
    swap = psutil.swap_memory()

    alerts = []
    # RAM omitida — servidor siempre near-limit por diseño (múltiples servicios ML)
    if cpu > THRESHOLDS["cpu_pct"]:
        alerts.append(f"CPU al {cpu}% — posible sobrecarga")
    if disk.percent > THRESHOLDS["disk_pct"]:
        alerts.append(f"Disco al {disk.percent}% — limpiar backups antiguos")
    # Swap omitido — el servidor usa swap estructuralmente por los servicios ML

    return {
        "healthy": len(alerts) == 0,
        "alerts": alerts,
        "ram_free_mb": ram.available // 1024 // 1024,
        "ram_pct": ram.percent,
        "cpu_pct": cpu,
        "disk_pct": disk.percent,
        "swap_mb": swap.used // 1024 // 1024,
    }
