from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class Bullet(BaseModel):
    text: str = Field(description="Single resume bullet, action-led, metric-bearing where possible.")
    tags: List[str] = Field(
        default_factory=list,
        description="Short keyword tags for selector matching (e.g. 'Python', 'ETL', 'Leadership').",
    )


class Experience(BaseModel):
    company: str
    role: str
    location: Optional[str] = None
    date: Optional[str] = None
    bullet_pool: List[Bullet] = Field(default_factory=list)


class Project(BaseModel):
    name: str
    location: Optional[str] = None
    link: Optional[str] = None
    bullet_pool: List[Bullet] = Field(default_factory=list)


class Education(BaseModel):
    institution: str
    location: Optional[str] = None
    degree: Optional[str] = None
    gpa: Optional[str] = None
    coursework: List[str] = Field(default_factory=list)


class PersonalInfo(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    linkedin: Optional[str] = None
    github: Optional[str] = None


class SkillsSection(BaseModel):
    category: str
    items: List[str]


class MasterCV(BaseModel):
    personal_info: PersonalInfo
    professional_summary: Optional[str] = None
    skills: List[SkillsSection] = Field(default_factory=list)
    experience: List[Experience] = Field(default_factory=list)
    projects: List[Project] = Field(default_factory=list)
    education: List[Education] = Field(default_factory=list)


class JDAnalysis(BaseModel):
    role_title: str = Field(description="Target role name, e.g. 'Analytics Engineer'.")
    primary_tech_stack: List[str] = Field(
        description="Concrete tools, languages, platforms mentioned in JD (e.g. 'SQL', 'dbt', 'Databricks')."
    )
    core_impact_areas: List[str] = Field(
        description="High-level outcomes the role drives (e.g. 'Data modeling', 'Stakeholder reporting')."
    )
    must_have_keywords: List[str] = Field(
        description="ATS-critical keywords that should appear verbatim in selected bullets where truthful."
    )
    domain: Optional[str] = Field(
        default=None, description="Industry/domain context (e.g. 'Insurance', 'Fintech'). None if not specified."
    )


class SelectedBullet(BaseModel):
    source_index: int = Field(
        description="Zero-based index into the source bullet_pool that this selection refers to."
    )
    compressed_text: str = Field(
        description=(
            "Final bullet text, target 140-200 chars. Must preserve concrete metrics verbatim "
            "and weave in JD keywords where truthful. Must contain at least one quantifier. "
            "Logical content must match source — no fabrication of tools, scope, or outcomes."
        )
    )
    relevance_score: int = Field(
        ge=1, le=10, description="Selector's 1-10 relevance score for the chosen bullet."
    )


class RoleSelection(BaseModel):
    company: str = Field(description="Must match source experience.company exactly.")
    role: str = Field(description="Must match source experience.role exactly.")
    selected_bullets: List[SelectedBullet]


class ProjectSelection(BaseModel):
    name: str = Field(description="Must match source project.name exactly.")
    selected_bullets: List[SelectedBullet]


class TailoredSelection(BaseModel):
    jd_analysis: JDAnalysis
    experience_selections: List[RoleSelection]
    project_selections: List[ProjectSelection]


class RewrittenSummary(BaseModel):
    summary: str = Field(
        description=(
            "Tailored 2-3 sentence professional summary. Same person, same facts as source — "
            "re-angled toward the JD. Includes role-relevant tech keywords and domain framing "
            "where truthful. 260-380 characters."
        )
    )


class EnrichedSkills(BaseModel):
    skills: List[SkillsSection] = Field(
        description=(
            "Enriched skills sections. Each original category is preserved with original items "
            "in original order; JD-relevant additions appended at end of the most topically "
            "fitting category. Only add a skill if (a) the candidate's bullet pool shows direct "
            "evidence of using it, OR (b) it is universally assumed for the candidate's role "
            "(e.g. Git for software engineers). Prefer broader concepts ('data modeling', "
            "'dashboarding') over specific tools when evidence is weak."
        )
    )


class PlacementLocation(BaseModel):
    section: str  # "summary" | "skills" | "experience" | "project"
    label: str  # e.g. "Summary" | "Skills > BI" | "Data Engineer @ Incedo, bullet 1"
    is_new: bool = False  # True if added (vs already present in master)
    snippet: str = ""  # short excerpt showing context


class KeywordPlacement(BaseModel):
    keyword: str
    bucket: str  # "primary_tech" | "core_impact" | "must_have" | "domain"
    locations: List[PlacementLocation] = Field(default_factory=list)


class SponsorshipInfo(BaseModel):
    # 'unspecified' = JD never mentions visa/sponsorship terms
    # 'mentioned'   = mentioned but stance unclear (e.g. "must be authorized to work")
    # 'available'   = JD explicitly states sponsorship is offered
    # 'not_available' = JD explicitly states sponsorship is NOT offered
    status: str = "unspecified"
    evidence: List[str] = Field(default_factory=list)


class LanguageConstraint(BaseModel):
    language: str
    # 'native' | 'fluent' | 'professional' | 'conversational' | 'basic' | 'bilingual' | ''
    level: str = ""
    required: bool = True  # False = preferred / nice-to-have
    evidence: str = ""


class PipelineReport(BaseModel):
    analysis: JDAnalysis
    placements: List[KeywordPlacement]
    skill_additions: List[str] = Field(default_factory=list)  # human-readable "Category: item"
    sponsorship: SponsorshipInfo = Field(default_factory=SponsorshipInfo)
    languages: List[LanguageConstraint] = Field(default_factory=list)


class GenerationRecord(BaseModel):
    id: str
    company: str
    jd: str
    source_url: Optional[str] = None
    cv: MasterCV
    report: PipelineReport
    pdf_filename: str = ""
    created_at: str  # ISO-8601 UTC


class GenerationSummary(BaseModel):
    """Lightweight row for the sidebar list — no CV/report blob."""
    id: str
    company: str
    source_url: Optional[str] = None
    pdf_filename: str = ""
    created_at: str
    applied: bool = False


class AppliedRecord(BaseModel):
    id: str
    company: str
    job_title: str = ""
    job_link: str = ""
    applied_at: str  # ISO-8601 date
    status: str = "applied"  # applied | assessment | interview | offer | rejected | withdrew
    notes: str = ""
    generation_id: Optional[str] = None


