from .AutonaDirHelper import AutonaResolve
import json
from loguru import logger

def get_supported_providers():
    try:
        versions = json.load(
            open(AutonaResolve('./assets/versions.json'), encoding='utf-8'))
    except Exception as e:
        logger.error("Local version file not found")
        versions = {}
    
    return(versions.get("supported_providers", []))