from pathlib import Path

def test_control_plane_image_includes_migrations():
    dockerfile=Path('Dockerfile').read_text()
    assert 'COPY migrations ./migrations' in dockerfile
    assert 'COPY alembic.ini ./alembic.ini' in dockerfile
    assert Path('migrations/env.py').exists()
    assert Path('migrations/versions/0001_blazenxt_v1_baseline.py').exists()
