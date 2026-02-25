#!/usr/bin/env python3
"""
One-time script to populate the equipment table from cfg_equipment (config tree).
Run this once to seed the maintenance.equipment table with all equipment from the config.

Usage:
  python scripts/populate_equipment_table.py
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.orm import Session
from sunny_scada.core.settings import Settings
from sunny_scada.db.models import Equipment, CfgEquipment
from sunny_scada.db.session import create_engine_and_sessionmaker


def populate_equipment_from_config():
    """
    Read all CfgEquipment records from the config tree and create Equipment
    records in the maintenance table if they don't already exist.
    """
    # Load settings
    settings = Settings()
    
    # Create engine and sessionmaker
    db_runtime = create_engine_and_sessionmaker(settings.database_url)
    engine = db_runtime.engine
    SessionLocal = db_runtime.SessionLocal
    
    with Session(engine) as db:
        # Get all equipment from config tree
        cfg_equipment_list = db.query(CfgEquipment).all()
        
        if not cfg_equipment_list:
            print("No equipment found in config tree.")
            return
        
        print(f"Found {len(cfg_equipment_list)} equipment records in config tree.")
        
        created_count = 0
        skipped_count = 0
        
        for cfg_eq in cfg_equipment_list:
            # Check if equipment with this ID already exists
            existing = db.query(Equipment).filter(Equipment.id == cfg_eq.id).one_or_none()
            
            if existing:
                print(f"  ➜ Equipment id={cfg_eq.id} ({cfg_eq.name}) already exists - skipping")
                skipped_count += 1
                continue
            
            # Create new Equipment record
            try:
                equipment_code = f"EQ-{cfg_eq.id:04d}"
                
                # Check if code already exists (e.g., from auto-creation)
                existing_by_code = db.query(Equipment).filter(
                    Equipment.equipment_code == equipment_code
                ).one_or_none()
                
                if existing_by_code:
                    print(f"  ➜ Equipment code {equipment_code} already exists (id={existing_by_code.id}) - skipping")
                    skipped_count += 1
                    continue
                
                equipment = Equipment(
                    id=cfg_eq.id,
                    equipment_code=equipment_code,
                    name=cfg_eq.name or f"Equipment {cfg_eq.id}",
                    location=None,
                    description=f"Auto-populated from config tree (type: {cfg_eq.type})",
                    vendor_id=None,
                    is_active=True,
                    meta={"cfg_type": cfg_eq.type},
                )
                db.add(equipment)
                db.flush()
                print(f"  ✓ Created equipment id={cfg_eq.id} ({cfg_eq.name}) with code {equipment_code}")
                created_count += 1
            except Exception as e:
                print(f"  ✗ Error creating equipment id={cfg_eq.id} ({cfg_eq.name}): {e}")
                db.rollback()
                raise
        
        # Commit all changes
        try:
            db.commit()
            print(f"\n✓ Success! Created {created_count} equipment records, skipped {skipped_count}")
        except Exception as e:
            print(f"\n✗ Commit failed: {e}")
            db.rollback()
            raise


if __name__ == "__main__":
    print("=" * 60)
    print("Equipment Table Population Script")
    print("=" * 60)
    populate_equipment_from_config()
    print("=" * 60)
