from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Set, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from sunny_scada.db.models import CfgAccessGrant, CfgContainer, CfgDataPoint, CfgEquipment, User


RESOURCE_TYPES = ("plc", "container", "equipment", "datapoint")
ACCESS_LEVELS = ("read", "write")


@dataclass
class EffectiveAccess:
    """Computed access sets for a user.

    This is allow-only RBAC.
    - write implies read
    """

    read_plc_ids: Set[int]
    write_plc_ids: Set[int]

    read_container_ids: Set[int]
    write_container_ids: Set[int]

    read_equipment_ids: Set[int]
    write_equipment_ids: Set[int]

    read_datapoint_ids: Set[int]
    write_datapoint_ids: Set[int]

    def can_read(self, resource_type: str, resource_id: int) -> bool:
        if resource_type == "plc":
            return resource_id in self.read_plc_ids
        if resource_type == "container":
            return resource_id in self.read_container_ids
        if resource_type == "equipment":
            return resource_id in self.read_equipment_ids
        if resource_type == "datapoint":
            return resource_id in self.read_datapoint_ids
        return False

    def can_write(self, resource_type: str, resource_id: int) -> bool:
        if resource_type == "plc":
            return resource_id in self.write_plc_ids
        if resource_type == "container":
            return resource_id in self.write_container_ids
        if resource_type == "equipment":
            return resource_id in self.write_equipment_ids
        if resource_type == "datapoint":
            return resource_id in self.write_datapoint_ids
        return False


class AccessControlService:
    """RBAC + per-user overrides for the DB-backed System Configuration tree."""

    # -----------------
    # Grants CRUD
    # -----------------

    def list_role_grants(self, db: Session, *, role_id: int) -> list[CfgAccessGrant]:
        return (
            db.query(CfgAccessGrant)
            .filter(CfgAccessGrant.role_id == int(role_id))
            .order_by(CfgAccessGrant.id.asc())
            .all()
        )

    def list_user_grants(self, db: Session, *, user_id: int) -> list[CfgAccessGrant]:
        return (
            db.query(CfgAccessGrant)
            .filter(CfgAccessGrant.user_id == int(user_id))
            .order_by(CfgAccessGrant.id.asc())
            .all()
        )

    def upsert_grant(
        self,
        db: Session,
        *,
        role_id: int | None = None,
        user_id: int | None = None,
        resource_type: str,
        resource_id: int,
        access_level: str,
        include_descendants: bool = True,
        created_by_user_id: int | None = None,
    ) -> CfgAccessGrant:
        """Create or update a grant (idempotent per principal+resource)."""

        if (role_id is None) == (user_id is None):
            raise ValueError("Exactly one of role_id or user_id must be set")

        resource_type = str(resource_type).strip().lower()
        if resource_type not in RESOURCE_TYPES:
            raise ValueError("Invalid resource_type")

        access_level = str(access_level).strip().lower()
        if access_level not in ACCESS_LEVELS:
            raise ValueError("Invalid access_level")

        resource_id = int(resource_id)
        if resource_id <= 0:
            raise ValueError("resource_id must be positive")

        include_descendants = bool(include_descendants)
        if resource_type == "datapoint":
            # irrelevant for datapoints
            include_descendants = False

        q = db.query(CfgAccessGrant)
        if role_id is not None:
            q = q.filter(
                CfgAccessGrant.role_id == int(role_id),
                CfgAccessGrant.resource_type == resource_type,
                CfgAccessGrant.resource_id == resource_id,
            )
        else:
            q = q.filter(
                CfgAccessGrant.user_id == int(user_id),
                CfgAccessGrant.resource_type == resource_type,
                CfgAccessGrant.resource_id == resource_id,
            )

        existing = q.one_or_none()
        if existing:
            existing.access_level = access_level
            existing.include_descendants = include_descendants
            db.add(existing)
            db.commit()
            db.refresh(existing)
            return existing

        g = CfgAccessGrant(
            role_id=int(role_id) if role_id is not None else None,
            user_id=int(user_id) if user_id is not None else None,
            resource_type=resource_type,
            resource_id=resource_id,
            access_level=access_level,
            include_descendants=include_descendants,
            created_by_user_id=int(created_by_user_id) if created_by_user_id is not None else None,
        )
        db.add(g)
        db.commit()
        db.refresh(g)
        return g

    def delete_grant(
        self,
        db: Session,
        *,
        grant_id: int,
        role_id: int | None = None,
        user_id: int | None = None,
    ) -> None:
        g = db.query(CfgAccessGrant).filter(CfgAccessGrant.id == int(grant_id)).one_or_none()
        if not g:
            raise ValueError("Grant not found")
        if role_id is not None and g.role_id != int(role_id):
            raise ValueError("Grant does not belong to role")
        if user_id is not None and g.user_id != int(user_id):
            raise ValueError("Grant does not belong to user")
        db.delete(g)
        db.commit()

    def clear_role_grants(self, db: Session, *, role_id: int) -> int:
        q = db.query(CfgAccessGrant).filter(CfgAccessGrant.role_id == int(role_id))
        n = q.count()
        q.delete(synchronize_session=False)
        db.commit()
        return int(n)

    def clear_user_grants(self, db: Session, *, user_id: int) -> int:
        q = db.query(CfgAccessGrant).filter(CfgAccessGrant.user_id == int(user_id))
        n = q.count()
        q.delete(synchronize_session=False)
        db.commit()
        return int(n)

    # -----------------
    # Effective access
    # -----------------

    def _effective_access_from_grants(self, db: Session, grants: list[CfgAccessGrant]) -> EffectiveAccess:
        """Compute effective access from a pre-filtered list of grants."""

        # Build lightweight relationship maps (entire graph).
        containers_rows = db.query(CfgContainer.id, CfgContainer.plc_id).all()
        equipment_rows = db.query(CfgEquipment.id, CfgEquipment.container_id).all()
        dp_rows = db.query(CfgDataPoint.id, CfgDataPoint.owner_type, CfgDataPoint.owner_id).all()

        containers_by_plc: Dict[int, Set[int]] = {}
        container_to_plc: Dict[int, int] = {}
        for cid, plc_id in containers_rows:
            container_to_plc[int(cid)] = int(plc_id)
            containers_by_plc.setdefault(int(plc_id), set()).add(int(cid))

        equipment_by_container: Dict[int, Set[int]] = {}
        equipment_to_container: Dict[int, int] = {}
        for eid, container_id in equipment_rows:
            equipment_to_container[int(eid)] = int(container_id)
            equipment_by_container.setdefault(int(container_id), set()).add(int(eid))

        datapoints_by_owner: Dict[Tuple[str, int], Set[int]] = {}
        dp_to_owner: Dict[int, Tuple[str, int]] = {}
        for dp_id, owner_type, owner_id in dp_rows:
            key = (str(owner_type), int(owner_id))
            datapoints_by_owner.setdefault(key, set()).add(int(dp_id))
            dp_to_owner[int(dp_id)] = (str(owner_type), int(owner_id))

        read_plc: Set[int] = set()
        write_plc: Set[int] = set()
        read_container: Set[int] = set()
        write_container: Set[int] = set()
        read_equipment: Set[int] = set()
        write_equipment: Set[int] = set()
        read_dp: Set[int] = set()
        write_dp: Set[int] = set()

        def add_ids(target_read: Set[int], target_write: Set[int], ids: Iterable[int], level: str) -> None:
            for rid in ids:
                target_read.add(int(rid))
                if level == "write":
                    target_write.add(int(rid))

        for g in grants:
            rtype = str(g.resource_type)
            rid = int(g.resource_id)
            level = str(g.access_level)
            include = bool(g.include_descendants)

            if rtype == "plc":
                add_ids(read_plc, write_plc, [rid], level)
                if include:
                    c_ids = containers_by_plc.get(rid, set())
                    add_ids(read_container, write_container, c_ids, level)

                    e_ids: Set[int] = set()
                    for c_id in c_ids:
                        e_ids |= equipment_by_container.get(int(c_id), set())
                    add_ids(read_equipment, write_equipment, e_ids, level)

                    dp_ids: Set[int] = set()
                    dp_ids |= datapoints_by_owner.get(("plc", rid), set())
                    for c_id in c_ids:
                        dp_ids |= datapoints_by_owner.get(("container", int(c_id)), set())
                    for e_id in e_ids:
                        dp_ids |= datapoints_by_owner.get(("equipment", int(e_id)), set())
                    add_ids(read_dp, write_dp, dp_ids, level)

            elif rtype == "container":
                add_ids(read_container, write_container, [rid], level)
                if include:
                    e_ids = equipment_by_container.get(rid, set())
                    add_ids(read_equipment, write_equipment, e_ids, level)

                    dp_ids: Set[int] = set()
                    dp_ids |= datapoints_by_owner.get(("container", rid), set())
                    for e_id in e_ids:
                        dp_ids |= datapoints_by_owner.get(("equipment", int(e_id)), set())
                    add_ids(read_dp, write_dp, dp_ids, level)

            elif rtype == "equipment":
                add_ids(read_equipment, write_equipment, [rid], level)
                if include:
                    dp_ids = datapoints_by_owner.get(("equipment", rid), set())
                    add_ids(read_dp, write_dp, dp_ids, level)

            elif rtype == "datapoint":
                add_ids(read_dp, write_dp, [rid], level)

        # write implies read
        read_plc |= write_plc
        read_container |= write_container
        read_equipment |= write_equipment
        read_dp |= write_dp

        # Add ancestors for tree navigation (read-only escalation).
        changed = True
        while changed:
            changed = False

            # container -> plc
            for c_id in list(read_container):
                plc_id = container_to_plc.get(int(c_id))
                if plc_id is not None and plc_id not in read_plc:
                    read_plc.add(int(plc_id))
                    changed = True

            # equipment -> container
            for e_id in list(read_equipment):
                c_id = equipment_to_container.get(int(e_id))
                if c_id is not None and c_id not in read_container:
                    read_container.add(int(c_id))
                    changed = True

            # datapoint -> owner
            for dp_id in list(read_dp):
                owner = dp_to_owner.get(int(dp_id))
                if not owner:
                    continue
                owner_type, owner_id = owner
                if owner_type == "plc":
                    if owner_id not in read_plc:
                        read_plc.add(int(owner_id))
                        changed = True
                elif owner_type == "container":
                    if owner_id not in read_container:
                        read_container.add(int(owner_id))
                        changed = True
                elif owner_type == "equipment":
                    if owner_id not in read_equipment:
                        read_equipment.add(int(owner_id))
                        changed = True

        return EffectiveAccess(
            read_plc_ids=read_plc,
            write_plc_ids=write_plc,
            read_container_ids=read_container,
            write_container_ids=write_container,
            read_equipment_ids=read_equipment,
            write_equipment_ids=write_equipment,
            read_datapoint_ids=read_dp,
            write_datapoint_ids=write_dp,
        )

    def effective_access(self, db: Session, user: User) -> EffectiveAccess:
        """Compute effective access for a user.

        Rules:
          - role grants UNION user grants
          - write implies read
          - include_descendants controls inheritance for plc/container/equipment
          - no denies (allow-only)

        Implementation note:
          - We *also* grant read access to ancestors of any readable node so the UI can
            render a complete navigation tree.
        """

        role_ids = [r.id for r in (user.roles or [])]

        if role_ids:
            grants = (
                db.query(CfgAccessGrant)
                .filter(or_(CfgAccessGrant.user_id == user.id, CfgAccessGrant.role_id.in_(role_ids)))
                .all()
            )
        else:
            grants = db.query(CfgAccessGrant).filter(CfgAccessGrant.user_id == user.id).all()

        return self._effective_access_from_grants(db, grants)

    def effective_access_for_role_ids(self, db: Session, *, role_ids: Iterable[int]) -> EffectiveAccess:
        """Compute effective access for a principal identified only by roles.

        This supports AppClient principals bound to a Role.
        """

        ids = []
        for r in role_ids or []:
            try:
                ri = int(r)
            except Exception:
                continue
            if ri > 0:
                ids.append(ri)

        if not ids:
            return self._effective_access_from_grants(db, [])

        grants = db.query(CfgAccessGrant).filter(CfgAccessGrant.role_id.in_(ids)).all()
        return self._effective_access_from_grants(db, grants)

    def can_read(self, db: Session, user: User, resource_type: str, resource_id: int) -> bool:
        ea = self.effective_access(db, user)
        return ea.can_read(str(resource_type).lower(), int(resource_id))

    def can_write(self, db: Session, user: User, resource_type: str, resource_id: int) -> bool:
        ea = self.effective_access(db, user)
        return ea.can_write(str(resource_type).lower(), int(resource_id))

    def can_read_for_roles(self, db: Session, *, role_ids: Iterable[int], resource_type: str, resource_id: int) -> bool:
        ea = self.effective_access_for_role_ids(db, role_ids=role_ids)
        return ea.can_read(str(resource_type).lower(), int(resource_id))

    def can_write_for_roles(self, db: Session, *, role_ids: Iterable[int], resource_type: str, resource_id: int) -> bool:
        ea = self.effective_access_for_role_ids(db, role_ids=role_ids)
        return ea.can_write(str(resource_type).lower(), int(resource_id))
