from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, DateTime, Enum, Boolean
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from datetime import datetime, timezone

Base = declarative_base()

def utc_now():
    return datetime.now(timezone.utc)

class Scope(Base):
    __tablename__ = 'scopes'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    type = Column(Enum("domains", "ip_range", name="scope_type"), nullable=False)
    tag = Column(String, nullable=True)
    scanned = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    resources = relationship("Resource", back_populates="scope", cascade="all, delete-orphan")


class Resource(Base):
    __tablename__ = 'resources'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    scope_id = Column(Integer, ForeignKey('scopes.id', ondelete="CASCADE"), nullable=False)
    type = Column(Enum("domain", "ip", name="resource_type"), nullable=False)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    scope = relationship("Scope", back_populates="resources")
    endpoints = relationship("Endpoint", back_populates="resource", cascade="all, delete-orphan")


class Endpoint(Base):
    __tablename__ = 'endpoints'

    id = Column(Integer, primary_key=True)
    resource_id = Column(Integer, ForeignKey('resources.id', ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    port = Column(Integer, nullable=True)
    scheme = Column(String, nullable=True)
    tech = Column(String, nullable=True)
    title = Column(String, nullable=True)
    response_code = Column(Integer, nullable=True)
    content_length = Column(Integer, nullable=True)
    words_count = Column(Integer, nullable=True)
    data = Column(String, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    resource = relationship("Resource", back_populates="endpoints")


# Database setup
engine = create_engine("sqlite:///scope_scan.db")
Base.metadata.create_all(engine)

Session = sessionmaker(bind=engine)
session = Session()