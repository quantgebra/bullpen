from typing import Optional

import sqlalchemy
from sqlalchemy import orm

import bullpen_sec_master.base as base


# ======================================================================================================================
class Symbology(base.Base):
    """
    A naming system such as IBKR or BBG. It names exchanges, futures roots and instruments alike, and stores only the
    symbols that differ from its parent's (or from the target's canonical name). contract_template is inherited too.
    """

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================
    __tablename__ = "symbologies"

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def create(
        session: orm.Session, code: str, parent: Optional["Symbology"] = None, contract_template: Optional[str] = None
    ) -> "Symbology":
        created_row: Symbology = Symbology(code=code, parent=parent, contract_template=contract_template)
        session.add(created_row)
        session.flush()
        return created_row

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def find_by_code(session: orm.Session, code: str) -> Optional["Symbology"]:
        select_statement: sqlalchemy.Select = sqlalchemy.select(Symbology).where(Symbology.code == code)
        result: Optional[Symbology] = session.scalars(select_statement).first()
        return result

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    id: orm.Mapped[int] = orm.mapped_column(primary_key=True)
    code: orm.Mapped[str] = orm.mapped_column(unique=True)
    parent_id: orm.Mapped[Optional[int]] = orm.mapped_column(sqlalchemy.ForeignKey("sec_master.symbologies.id"))
    parent: orm.Mapped[Optional["Symbology"]] = orm.relationship(remote_side=[id])
    contract_template: orm.Mapped[Optional[str]]

    # ------------------------------------------------------------------------------------------------------------------
    def get_chain(self) -> list["Symbology"]:
        """This symbology followed by its ancestors, most specific first."""
        result: list[Symbology] = []
        current_symbology: Optional[Symbology] = self
        while current_symbology is not None:
            result.append(current_symbology)
            current_symbology = current_symbology.parent
        return result
