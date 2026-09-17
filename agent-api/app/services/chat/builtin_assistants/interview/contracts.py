"""Versioned interview commands and model submissions; no execution logic."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InterviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class InterviewConfig(InterviewModel):
    job_title: str = Field(min_length=1, max_length=120)
    jd_text: str = Field(default="", max_length=30000)
    resume_file_id: str = Field(min_length=1, max_length=64)
    resume_notes: str = Field(default="", max_length=6000)
    jd_file_id: str | None = Field(default=None, max_length=64)
    question_count: int = Field(default=6, ge=3, le=12, strict=True)
    level: Literal["intern", "graduate", "experienced"] = "graduate"
    pressure_level: Literal["gentle", "normal", "challenging"] = "normal"

    @model_validator(mode="after")
    def require_jd(self):
        if not self.jd_text and not self.jd_file_id:
            raise ValueError("请填写岗位 JD 或上传 JD 文件。")
        if self.jd_file_id == self.resume_file_id:
            raise ValueError("简历与 JD 请使用不同的材料。")
        return self


class InterviewInput(InterviewModel):
    action: Literal["start", "answer", "skip", "pause", "resume", "finish", "retry", "hint"] = "answer"
    expected_version: int | None = Field(default=None, ge=0, strict=True)
    question_id: str | None = Field(default=None, min_length=1, max_length=64)
    config: InterviewConfig | None = None
    pressure_level: Literal["gentle", "normal", "challenging"] | None = None

    @model_validator(mode="after")
    def start_config_only(self):
        if self.action == "start" and self.config is None:
            raise ValueError("开始面试前请确认简历与岗位设置。")
        if self.action != "start" and self.config is not None:
            raise ValueError("面试开始后材料与设置已冻结；请新建面试修改设置。")
        if self.action != "start" and self.expected_version is None:
            raise ValueError("请携带当前面试版本，刷新进度后再提交。")
        if self.action in {"answer", "skip", "retry", "hint"} and not self.question_id:
            raise ValueError("请携带正在回答的题目 ID，避免回答错题。")
        return self


class SourceReference(InterviewModel):
    kind: Literal["resume", "jd", "answer"]
    quote: str = Field(min_length=2, max_length=500)
    file_id: str | None = Field(default=None, max_length=64)
    message_id: int | None = Field(default=None, ge=1, strict=True)


class InterviewQuestion(InterviewModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    type: Literal["behavioral", "professional", "pressure"]
    text: str = Field(min_length=4, max_length=1400)
    competency: str = Field(min_length=1, max_length=240)
    source_refs: list[SourceReference] = Field(min_length=1, max_length=8)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    parent_question_id: str | None = Field(default=None, max_length=64)


class AnswerEvidence(InterviewModel):
    message_id: int = Field(ge=1, strict=True)
    quote: str = Field(min_length=2, max_length=500)


class DimensionScore(InterviewModel):
    status: Literal["scored", "not_assessed", "insufficient_evidence", "unanswered"]
    score: int | None = Field(default=None, ge=0, le=100, strict=True)
    reason: str = Field(min_length=1, max_length=800)
    evidence: list[AnswerEvidence] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def score_requires_evidence(self):
        if self.status == "scored" and (self.score is None or not self.evidence):
            raise ValueError("评分必须包含 0–100 分及真实回答证据。")
        if self.status != "scored" and self.score is not None:
            raise ValueError("未考察、未回答或证据不足时分数必须为空。")
        return self


class EvaluationDimensions(InterviewModel):
    professional: DimensionScore
    logic: DimensionScore
    expression: DimensionScore


class InterviewEvaluation(InterviewModel):
    score_scale: Literal[100] = Field(default=100, description="本次评价采用百分制，固定为 100；不能提交旧版五分制。")
    dimensions: EvaluationDimensions
    feedback: str = Field(min_length=1, max_length=1800)
    strengths: list[str] = Field(default_factory=list, max_length=6)
    improvements: list[str] = Field(default_factory=list, max_length=6)
    sample_answer: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def bounded_points(self):
        if any(len(item) > 600 for item in self.strengths + self.improvements):
            raise ValueError("评价要点过长。")
        return self


class InterviewProfile(InterviewModel):
    summary: str = Field(min_length=1, max_length=2000)
    competencies: list[str] = Field(min_length=1, max_length=20)
    source_refs: list[SourceReference] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def bounded_competencies(self):
        if any(not item.strip() or len(item) > 240 for item in self.competencies):
            raise ValueError("能力名称不能为空或超过 240 字。")
        return self


class InterviewReview(InterviewModel):
    summary: str = Field(min_length=1, max_length=2200)
    strengths: list[str] = Field(default_factory=list, max_length=8)
    improvements: list[str] = Field(default_factory=list, max_length=8)
    next_steps: list[str] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def bounded_points(self):
        if any(len(item) > 800 for item in self.strengths + self.improvements + self.next_steps):
            raise ValueError("复盘要点过长。")
        return self


class InterviewTurnCommit(InterviewModel):
    expected_version: int = Field(ge=0, strict=True, description="每次提交必填的顶层参数，原样复制 get_interview_session.input.expected_version，不能省略或放进 evaluation。")
    question_id: str | None = Field(default=None, max_length=64, description="原样复制 get_interview_session.input.question_id；它是本轮被回答或控制的题目，不是 next_question.id。")
    profile: InterviewProfile | None = None
    question_bank: list[InterviewQuestion] = Field(default_factory=list, max_length=36)
    evaluation: InterviewEvaluation | None = None
    next_question: InterviewQuestion | None = None
    next_question_id: str | None = Field(default=None, min_length=1, max_length=64, description="选择已保存或本次 question_bank 中的题目时只传其 ID，由平台原样取题；不要重复生成 next_question。新追问才提交完整 next_question。二者互斥。")
    review: InterviewReview | None = None
    hint: str | None = Field(default=None, min_length=1, max_length=1200, description="仅 action=hint 时必填：当前题的简短思路提示，不给完整答案、不评分、不推进题目。")

    @model_validator(mode="after")
    def one_next_question(self):
        if self.next_question is not None and self.next_question_id is not None:
            raise ValueError("下一题只提交 next_question_id 或完整 next_question，不能同时提交。")
        return self


class InterviewDomainError(Exception):
    def __init__(self, detail: str, *, code: str = "interview_conflict", status_code: int = 409):
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.status_code = status_code
