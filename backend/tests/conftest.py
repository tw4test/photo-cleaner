import pytest
from app.core.database import init_db, SyncSessionLocal
from app.models.schema import Base, Root, Asset, File, Feature, VideoInfo, Candidate, Group, ActionLog, Setting

@pytest.fixture(autouse=True)
def clean_db():
    init_db()
    with SyncSessionLocal() as session:
        session.query(ActionLog).delete()
        session.query(Candidate).delete()
        session.query(Group).delete()
        session.query(Feature).delete()
        session.query(VideoInfo).delete()
        session.query(File).delete()
        session.query(Asset).delete()
        session.query(Root).delete()
        session.commit()
    yield
