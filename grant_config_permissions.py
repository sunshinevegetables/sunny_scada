#!/usr/bin/env python3
"""
Quick script to grant config:read and config:write permissions to a user.
Run this if a user can't access the Datapoint Meta view.
"""

import sys
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sunny_scada.db.models import User, Role, RolePermission

# Load settings
from sunny_scada.core.settings import Settings
settings = Settings()

# Create engine
db_url = settings.database_url
engine = create_engine(db_url)
logger = logging.getLogger(__name__)

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if len(sys.argv) < 2:
        logger.error("Usage: python grant_config_permissions.py <username>")
        logger.error("Example: python grant_config_permissions.py admin")
        logger.error("This grants config:read and config:write permissions to the user.")
        sys.exit(1)
    
    username = sys.argv[1]
    
    with Session(engine) as db:
        # Find user
        user = db.query(User).filter(User.username == username).one_or_none()
        if not user:
            logger.error("User '%s' not found", username)
            sys.exit(1)
        
        logger.info("Found user: %s (id=%s)", user.username, user.id)
        
        # Check if user has an admin role, if not create/grant one
        admin_role = db.query(Role).filter(Role.name == "admin").one_or_none()
        if admin_role:
            logger.info("Found admin role (id=%s)", admin_role.id)
            # Check if user already has admin role
            if admin_role not in user.roles:
                user.roles.append(admin_role)
                db.add(user)
                db.commit()
                logger.info("Granted admin role to user")
            else:
                logger.info("User already has admin role")
        else:
            logger.info("No admin role found, granting direct permissions...")
            # Grant permissions directly
            for perm_name in ["config:read", "config:write"]:
                existing = db.query(RolePermission).filter(
                    RolePermission.user_id == user.id,
                    RolePermission.permission == perm_name
                ).one_or_none()
                if not existing:
                    rp = RolePermission(user_id=user.id, permission=perm_name)
                    db.add(rp)
                    logger.info("Granted permission: %s", perm_name)
                else:
                    logger.info("User already has: %s", perm_name)
            db.commit()
        
        logger.info("Done! The user should now be able to access the Datapoint Meta view.")

if __name__ == "__main__":
    main()
