#!/usr/bin/env python3
"""Force-add config:read and config:write permissions to the admin user."""

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
    
    logger.info("Updating permissions for user: %s (id=%s)", admin_user.username, admin_user.id)
    
    # Find or create admin role
    admin_role = db.query(Role).filter(Role.name == "admin").one_or_none()
    if not admin_role:
        logger.info("Creating admin role...")
        admin_role = Role(name="admin", description="Admin role")
        db.add(admin_role)
        db.flush()
    
    # Ensure user has admin role
    if admin_role not in admin_user.roles:
        logger.info("Adding admin role to user...")
        admin_user.roles.append(admin_role)
    
    # Add all essential permissions to admin role
    required_perms = [
        "plc:read",
        "plc:write",
        "config:read",
        "config:write",
        "command:read",
        "command:write",
        "iqf:control",
        "alarms:*",
        "maintenance:*",
        "inventory:write",
        "users:admin",
        "roles:admin",
    ]
    
    existing_perms = {rp.permission for rp in admin_role.permissions}
    for perm_name in required_perms:
        if perm_name not in existing_perms:
            rp = RolePermission(role_id=admin_role.id, permission=perm_name)
            db.add(rp)
            logger.info("Added permission: %s", perm_name)
        else:
            logger.info("Already has: %s", perm_name)
    
    db.commit()
    logger.info("Done! Admin user now has all required permissions.")
    logger.info("Please reload the admin panel in your browser to see the changes.")
