import enum
from datetime import datetime, timezone
from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db import Base

def now(): return datetime.now(timezone.utc)
class Role(str,enum.Enum): user='user'; premium='premium'; admin='admin'; owner='owner'
class State(str,enum.Enum): pending='pending'; provisioning='provisioning'; running='running'; stopped='stopped'; failed='failed'; deleted='deleted'
class User(Base):
    __tablename__='users'
    id:Mapped[int]=mapped_column(primary_key=True)
    telegram_id:Mapped[int]=mapped_column(BigInteger,unique=True,index=True)
    username:Mapped[str|None]=mapped_column(String(64))
    display_name:Mapped[str]=mapped_column(String(128),default='User')
    role:Mapped[Role]=mapped_column(Enum(Role),default=Role.user)
    banned:Mapped[bool]=mapped_column(Boolean,default=False)
    quota:Mapped[int|None]=mapped_column(Integer)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
class Artifact(Base):
    __tablename__='artifacts'
    id:Mapped[int]=mapped_column(primary_key=True)
    owner_id:Mapped[int]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    filename:Mapped[str]=mapped_column(String(160)); content_type:Mapped[str]=mapped_column(String(100))
    sha256:Mapped[str]=mapped_column(String(64),index=True); size:Mapped[int]=mapped_column(Integer); data:Mapped[bytes]=mapped_column(LargeBinary)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
class Workload(Base):
    __tablename__='workloads'
    id:Mapped[int]=mapped_column(primary_key=True)
    user_id:Mapped[int]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    artifact_id:Mapped[int]=mapped_column(ForeignKey('artifacts.id',ondelete='RESTRICT'))
    name:Mapped[str]=mapped_column(String(80)); runtime:Mapped[str]=mapped_column(String(16)); entrypoint:Mapped[str]=mapped_column(String(160))
    state:Mapped[State]=mapped_column(Enum(State),default=State.pending)
    railway_service_id:Mapped[str|None]=mapped_column(String(80),unique=True); last_error:Mapped[str|None]=mapped_column(Text)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now); updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,onupdate=now)
    artifact:Mapped[Artifact]=relationship()
class RunnerToken(Base):
    __tablename__='runner_tokens'
    id:Mapped[int]=mapped_column(primary_key=True); workload_id:Mapped[int]=mapped_column(ForeignKey('workloads.id',ondelete='CASCADE'),index=True)
    token_hash:Mapped[str]=mapped_column(String(64),unique=True,index=True); expires_at:Mapped[datetime]=mapped_column(DateTime(timezone=True)); consumed_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True))
class AuditLog(Base):
    __tablename__='audit_logs'
    id:Mapped[int]=mapped_column(primary_key=True); actor_id:Mapped[int|None]=mapped_column(ForeignKey('users.id',ondelete='SET NULL'),index=True)
    action:Mapped[str]=mapped_column(String(80),index=True); target:Mapped[str]=mapped_column(String(160)); ip:Mapped[str]=mapped_column(String(64)); detail:Mapped[str]=mapped_column(Text,default='{}')
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,index=True)
class WorkloadVariable(Base):
    __tablename__='workload_variables'; __table_args__=(UniqueConstraint('workload_id','name'),)
    id:Mapped[int]=mapped_column(primary_key=True); workload_id:Mapped[int]=mapped_column(ForeignKey('workloads.id',ondelete='CASCADE'),index=True)
    name:Mapped[str]=mapped_column(String(80)); encrypted_value:Mapped[str]=mapped_column(Text); is_secret:Mapped[bool]=mapped_column(Boolean,default=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now); updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,onupdate=now)
class Backup(Base):
    __tablename__='backups'
    id:Mapped[int]=mapped_column(primary_key=True); workload_id:Mapped[int]=mapped_column(ForeignKey('workloads.id',ondelete='CASCADE'),index=True)
    name:Mapped[str]=mapped_column(String(100)); filename:Mapped[str]=mapped_column(String(160)); sha256:Mapped[str]=mapped_column(String(64)); size:Mapped[int]=mapped_column(Integer); data:Mapped[bytes]=mapped_column(LargeBinary)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
class WorkloadMember(Base):
    __tablename__='workload_members'; __table_args__=(UniqueConstraint('workload_id','user_id'),)
    id:Mapped[int]=mapped_column(primary_key=True); workload_id:Mapped[int]=mapped_column(ForeignKey('workloads.id',ondelete='CASCADE'),index=True); user_id:Mapped[int]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    permissions:Mapped[str]=mapped_column(Text,default='["view","logs"]'); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
    user:Mapped[User]=relationship()
class Schedule(Base):
    __tablename__='schedules'
    id:Mapped[int]=mapped_column(primary_key=True); workload_id:Mapped[int]=mapped_column(ForeignKey('workloads.id',ondelete='CASCADE'),index=True)
    name:Mapped[str]=mapped_column(String(100)); action:Mapped[str]=mapped_column(String(20)); interval_minutes:Mapped[int]=mapped_column(Integer); enabled:Mapped[bool]=mapped_column(Boolean,default=True)
    next_run:Mapped[datetime]=mapped_column(DateTime(timezone=True)); last_run:Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
class Notification(Base):
    __tablename__='notifications'
    id:Mapped[int]=mapped_column(primary_key=True); user_id:Mapped[int]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    title:Mapped[str]=mapped_column(String(120)); message:Mapped[str]=mapped_column(Text); read:Mapped[bool]=mapped_column(Boolean,default=False); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,index=True)
