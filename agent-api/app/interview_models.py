"""Interview business state belongs to the shared MySQL conversation domain."""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text, func

from app.core.database import Base


class InterviewSession(Base):
    __tablename__ = "agent_interview_session"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(String(64), primary_key=True)
    thread_id = Column(String(64), ForeignKey("ai_chat_threads.id", ondelete="CASCADE"), nullable=False, unique=True)
    user_id = Column(String(64), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=0)
    status = Column(String(24), nullable=False, default="preparing")
    pressure_level = Column(String(16), nullable=False, default="normal")
    config_json = Column(JSON, nullable=False)
    materials_json = Column(JSON, nullable=False)
    profile_json = Column(JSON, nullable=True)
    question_bank_json = Column(JSON, nullable=False, default=list)
    current_question_json = Column(JSON, nullable=True)
    review_json = Column(JSON, nullable=True)
    policy_version = Column(String(32), nullable=False, default="interview-v1")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class InterviewTurn(Base):
    __tablename__ = "agent_interview_turn"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(String(64), primary_key=True)
    session_id = Column(String(64), ForeignKey("agent_interview_session.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id = Column(String(64), nullable=False, unique=True)
    answer_message_id = Column(Integer, ForeignKey("ai_chat_messages.id", ondelete="CASCADE"), nullable=False)
    action = Column(String(16), nullable=False)
    expected_version = Column(Integer, nullable=False)
    committed_version = Column(Integer, nullable=True)
    question_id = Column(String(64), nullable=True)
    question_json = Column(JSON, nullable=True)
    input_json = Column(JSON, nullable=False)
    answer_text = Column(Text, nullable=False, default="")
    evaluation_json = Column(JSON, nullable=True)
    result_json = Column(JSON, nullable=True)
    assisted = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now())
    committed_at = Column(DateTime, nullable=True)
