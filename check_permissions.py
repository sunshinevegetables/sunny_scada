#!/usr/bin/env python3
"""Check what permissions the admin user has."""

import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sunny_scada.db.models import User, Role, RolePermission
from sunny_scada.core.settings import Settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

settings = Settings()
engine = create_engine(settings.database_url)

with Session(engine) as db:
    # Find admin user
    admin_user = db.query(User).filter(User.username == "admin").one_or_none()
    if not admin_user:
        logger.error("Admin user not found!")
        exit(1)
    
    logger.info("User: %s (id=%s)", admin_user.username, admin_user.id)
    logger.info("Active: %s", admin_user.is_active)
    logger.info("Roles: %s", [r.name for r in admin_user.roles])
    
    # Get role permissions
    all_perms = set()
    logger.info("Role permissions:")
    for role in admin_user.roles:
        logger.info("Role '%s':", role.name)
        role_perms = db.query(RolePermission).filter(RolePermission.role_id == role.id).all()
        if not role_perms:
            logger.info("(no permissions)")
        for perm in role_perms:
            logger.info("- %s", perm.permission)
            all_perms.add(perm.permission)
    
    logger.info("All permissions combined: %s", sorted(all_perms) if all_perms else "(none)")
    logger.info("Has 'config:read': %s", 'config:read' in all_perms)
    logger.info("Has 'config:write': %s", 'config:write' in all_perms)

