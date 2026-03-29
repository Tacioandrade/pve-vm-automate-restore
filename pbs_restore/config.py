import os
from dotenv import load_dotenv
from pbs_restore.exceptions import ConfigurationError

def load_config():
    load_dotenv()
    required = [
        "PROXMOX_URL", "PROXMOX_NODE", "PROXMOX_USER", "PROXMOX_PASSWORD", 
        "BACKUP_STORAGE", "RESTORE_STORAGE", "VM_RESTORE_COUNT",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SCREENSHOT_WAIT_MINUTES",
        "PROXMOX_TIMEOUT", "AUTO_START_VM"
    ]
    
    # Opcionais dependendo do ANALYZE_WITH_GEMINI
    analyze_with_gemini = os.getenv("ANALYZE_WITH_GEMINI", "False").lower() == "true"
    if analyze_with_gemini:
        required.append("GEMINI_API_KEY")
        required.append("GEMINI_MODEL")

    for req in required:
        if not os.getenv(req):
            raise ConfigurationError(f"Faltando variável paramétrica no .env: {req}")

    from pbs_restore.models import AppConfig
    
    try:
        vm_restore_count = int(os.getenv("VM_RESTORE_COUNT", "0"))
        screenshot_wait_minutes = int(os.getenv("SCREENSHOT_WAIT_MINUTES", "0"))
        proxmox_timeout = int(os.getenv("PROXMOX_TIMEOUT", "60"))
    except ValueError:
         raise ConfigurationError("VM_RESTORE_COUNT, SCREENSHOT_WAIT_MINUTES e PROXMOX_TIMEOUT devem ser números inteiros")

    return AppConfig(
        proxmox_url=os.getenv("PROXMOX_URL"),
        proxmox_node=os.getenv("PROXMOX_NODE"),
        proxmox_user=os.getenv("PROXMOX_USER"),
        proxmox_password=os.getenv("PROXMOX_PASSWORD"),
        backup_storage=os.getenv("BACKUP_STORAGE"),
        restore_storage=os.getenv("RESTORE_STORAGE"),
        vm_restore_count=vm_restore_count,
        screenshot_wait_minutes=screenshot_wait_minutes,
        proxmox_timeout=proxmox_timeout,
        auto_start_vm=os.getenv("AUTO_START_VM", "True").lower() == "true",
        analyze_with_gemini=analyze_with_gemini,
        max_workers=int(os.getenv("MAX_WORKERS", "2")),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),

        gemini_model=os.getenv("GEMINI_MODEL", "gemini-1.5-flash"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
        log_level=os.getenv("LOG_LEVEL", "INFO")
    )



