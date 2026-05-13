"""Remove the TEST-PROBE SOP we created during smoke testing."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main() -> None:
    from app.database import SessionLocal
    from app.models import SOP, SOPVersion, KnowledgeChunk

    db = SessionLocal()
    try:
        sop = (
            db.query(SOP)
            .filter(SOP.title == "probe")
            .first()
        )
        if not sop:
            print("no probe SOP found")
            return
        sid = sop.id
        sop.current_version_id = None
        db.flush()
        db.query(KnowledgeChunk).filter(
            KnowledgeChunk.entity_type == "sop",
            KnowledgeChunk.entity_id == sid,
        ).delete(synchronize_session=False)
        db.query(SOPVersion).filter(SOPVersion.sop_id == sid).delete(synchronize_session=False)
        db.query(SOP).filter(SOP.id == sid).delete(synchronize_session=False)
        db.commit()
        print(f"removed probe SOP id={sid}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
