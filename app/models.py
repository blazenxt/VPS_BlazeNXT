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
class AuthIdentity(Base):
    __tablename__='auth_identities'; __table_args__=(UniqueConstraint('provider','subject'),)
    id:Mapped[int]=mapped_column(primary_key=True); user_id:Mapped[int]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    provider:Mapped[str]=mapped_column(String(20),index=True); subject:Mapped[str]=mapped_column(String(255)); email:Mapped[str|None]=mapped_column(String(320),index=True); display_name:Mapped[str|None]=mapped_column(String(128)); avatar_url:Mapped[str|None]=mapped_column(Text)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now); last_login_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
    user:Mapped[User]=relationship()
class AuthTokenUse(Base):
    __tablename__='auth_token_uses'
    id:Mapped[int]=mapped_column(primary_key=True); token_hash:Mapped[str]=mapped_column(String(64),unique=True,index=True); used_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
class ApiKey(Base):
    __tablename__='api_keys'
    id:Mapped[int]=mapped_column(primary_key=True); user_id:Mapped[int]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    name:Mapped[str]=mapped_column(String(80)); prefix:Mapped[str]=mapped_column(String(16),index=True); key_hash:Mapped[str]=mapped_column(String(64),unique=True,index=True); scopes:Mapped[str]=mapped_column(Text,default='["servers:read"]'); revoked:Mapped[bool]=mapped_column(Boolean,default=False)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now); last_used_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True))
class WorkloadAllocation(Base):
    __tablename__='workload_allocations'
    id:Mapped[int]=mapped_column(primary_key=True); workload_id:Mapped[int]=mapped_column(ForeignKey('workloads.id',ondelete='CASCADE'),unique=True,index=True)
    cpu_vcpus:Mapped[str]=mapped_column(String(16),default='0.5'); memory_mb:Mapped[int]=mapped_column(Integer,default=512); replicas:Mapped[int]=mapped_column(Integer,default=1); restart_policy:Mapped[str]=mapped_column(String(30),default='ON_FAILURE'); restart_retries:Mapped[int]=mapped_column(Integer,default=5); suspended:Mapped[bool]=mapped_column(Boolean,default=False); maintenance:Mapped[bool]=mapped_column(Boolean,default=False); updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,onupdate=now)
class ManagedDatabase(Base):
    __tablename__='managed_databases'
    id:Mapped[int]=mapped_column(primary_key=True); workload_id:Mapped[int]=mapped_column(ForeignKey('workloads.id',ondelete='CASCADE'),index=True); engine:Mapped[str]=mapped_column(String(20),default='postgresql'); railway_service_id:Mapped[str]=mapped_column(String(80),unique=True); railway_volume_id:Mapped[str|None]=mapped_column(String(80)); service_name:Mapped[str]=mapped_column(String(80)); database_name:Mapped[str]=mapped_column(String(63)); username:Mapped[str]=mapped_column(String(63)); encrypted_password:Mapped[str]=mapped_column(Text); state:Mapped[str]=mapped_column(String(20),default='provisioning'); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
class WorkloadWebhook(Base):
    __tablename__='workload_webhooks'
    id:Mapped[int]=mapped_column(primary_key=True); workload_id:Mapped[int]=mapped_column(ForeignKey('workloads.id',ondelete='CASCADE'),index=True)
    url:Mapped[str]=mapped_column(Text); encrypted_secret:Mapped[str]=mapped_column(Text); events:Mapped[str]=mapped_column(Text,default='["*"]'); enabled:Mapped[bool]=mapped_column(Boolean,default=True); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
class WebhookDelivery(Base):
    __tablename__='webhook_deliveries'
    id:Mapped[int]=mapped_column(primary_key=True); webhook_id:Mapped[int]=mapped_column(ForeignKey('workload_webhooks.id',ondelete='CASCADE'),index=True); event:Mapped[str]=mapped_column(String(80)); status_code:Mapped[int|None]=mapped_column(Integer); success:Mapped[bool]=mapped_column(Boolean,default=False); error:Mapped[str|None]=mapped_column(Text); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,index=True)
class UserSecurity(Base):
    __tablename__='user_security'
    id:Mapped[int]=mapped_column(primary_key=True); user_id:Mapped[int]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),unique=True,index=True); encrypted_totp_secret:Mapped[str]=mapped_column(Text); enabled:Mapped[bool]=mapped_column(Boolean,default=False); encrypted_recovery_codes:Mapped[str]=mapped_column(Text); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
class StagedChange(Base):
    __tablename__='staged_changes'
    id:Mapped[int]=mapped_column(primary_key=True); user_id:Mapped[int]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True); workload_id:Mapped[int]=mapped_column(ForeignKey('workloads.id',ondelete='CASCADE'),index=True); environment:Mapped[str]=mapped_column(String(40),default='production'); kind:Mapped[str]=mapped_column(String(30)); payload:Mapped[str]=mapped_column(Text); status:Mapped[str]=mapped_column(String(20),default='pending',index=True); commit_message:Mapped[str|None]=mapped_column(String(200)); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,index=True); applied_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True))
class WorkloadDomain(Base):
    __tablename__='workload_domains'
    id:Mapped[int]=mapped_column(primary_key=True); workload_id:Mapped[int]=mapped_column(ForeignKey('workloads.id',ondelete='CASCADE'),index=True); domain:Mapped[str]=mapped_column(String(253)); railway_domain_id:Mapped[str|None]=mapped_column(String(80)); kind:Mapped[str]=mapped_column(String(20),default='custom'); dns_records:Mapped[str]=mapped_column(Text,default='[]'); status:Mapped[str]=mapped_column(String(30),default='pending'); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
class SupportTicket(Base):
    __tablename__='support_tickets'
    id:Mapped[int]=mapped_column(primary_key=True); user_id:Mapped[int]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    category:Mapped[str]=mapped_column(String(30),default='technical'); subject:Mapped[str]=mapped_column(String(120)); message:Mapped[str]=mapped_column(Text); status:Mapped[str]=mapped_column(String(20),default='open',index=True)
    admin_note:Mapped[str|None]=mapped_column(Text); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,index=True); updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,onupdate=now)
    user:Mapped[User]=relationship()
