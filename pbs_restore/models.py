from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

@dataclass
class RestorationResult:
    vmid: str
    vm_name: str = "Desconhecido"
    status: str = "Pendente"
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration: str = ""
    backup_volid: str = ""
    ia_status: str = "Não verificado"
    error_details: str = ""
    alert_msg: str = ""

@dataclass
class AppConfig:
    proxmox_url: str
    proxmox_node: str
    proxmox_user: str
    proxmox_password: str
    backup_storage: str
    restore_storage: str
    vm_restore_count: int
    screenshot_wait_minutes: int
    proxmox_timeout: int
    auto_start_vm: bool
    analyze_with_gemini: bool
    max_workers: int = 2
    gemini_api_key: Optional[str] = None

    gemini_model: str = "gemini-1.5-flash"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    log_level: str = "INFO"
