from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class Bullet(BaseModel):
    text: str = Field(description="Single resume bullet, action-led, metric-bearing where possible.")
    text_rac: str = Field(
        default="",
        description=(
            "Result–Action–Context phrasing of the same fact (lead with the outcome, "
            "then what you did, then the context). Populated lazily via "
            "POST /api/master/generate-rac. Selector ignores this field today; it "
            "exists so you can review or copy the RAC version manually."
        ),
    )
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
    # Per-role override for how many bullets the selector should keep. None =
    # use the selector's default (3). Set to 5 on a flagship role so it gets
    # more real estate in the tailored CV.
    target_bullets_override: Optional[int] = None


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


# ─── Outreach ────────────────────────────────────────────────────────────
class EmploymentEntry(BaseModel):
    title: Optional[str] = None
    organization_name: Optional[str] = None
    start_date: Optional[str] = None  # YYYY-MM
    end_date: Optional[str] = None
    current: bool = False


class EducationEntry(BaseModel):
    school: Optional[str] = None
    degree: Optional[str] = None


class ContactCandidate(BaseModel):
    """Raw contact data from the provider (e.g. Apollo)."""
    apollo_id: Optional[str] = None
    name: str
    title: str = ""
    headline: str = ""
    linkedin_url: Optional[str] = None
    email: Optional[str] = None
    email_status: Optional[str] = None  # 'verified' | 'guessed' | 'locked'
    organization_name: str = ""
    departments: List[str] = Field(default_factory=list)
    seniority: Optional[str] = None
    employment_history: List[EmploymentEntry] = Field(default_factory=list)
    education: List[EducationEntry] = Field(default_factory=list)
    photo_url: Optional[str] = None


class ScoredContact(BaseModel):
    contact: ContactCandidate
    score: int  # 0-100
    score_breakdown: dict = Field(default_factory=dict)  # signal → points
    tenure_months: int = 0
    category: str = ""  # 'recruiter' | 'hiring_manager' | 'team_ic' | 'other'
    shared_signals: List[str] = Field(default_factory=list)  # human-readable bits we'd weave in


class OutreachDraft(BaseModel):
    linkedin_note: str  # <= ~300 chars
    email_subject: str
    email_body: str


class OutreachContact(BaseModel):
    scored: ScoredContact
    draft: OutreachDraft


class OutreachRecord(BaseModel):
    generation_id: str
    company: str
    role_title: str = ""
    created_at: str
    contacts: List[OutreachContact] = Field(default_factory=list)


# ─── Job discovery ───────────────────────────────────────────────────────
class JobPreferences(BaseModel):
    """User's target-role preferences. Drives Apify queries + hard filters."""
    roles: List[str] = Field(default_factory=list)        # e.g. ["Data Engineer", "Analytics Engineer"]
    locations: List[str] = Field(default_factory=list)    # e.g. ["United States", "Remote"]
    remote_ok: bool = True
    salary_min_usd: Optional[int] = None                  # annual; jobs below get score penalty (not hard-filter)
    visa_sponsorship_needed: bool = False                 # if True, jobs marked "no sponsorship" are rejected
    my_languages: List[str] = Field(default_factory=lambda: ["English"])
    max_required_yoe: Optional[int] = None                # if set, jobs requiring more years are rejected
    keywords_include: List[str] = Field(default_factory=list)  # title/JD must contain at least one (OR)
    keywords_exclude: List[str] = Field(default_factory=list)  # title/JD containing any → reject
    companies_include: List[str] = Field(default_factory=list)
    companies_exclude: List[str] = Field(default_factory=list)
    post_age_days_max: int = 7
    autogen_top_n: int = 5
    autogen_min_score: int = 70


class DiscoveredJob(BaseModel):
    id: str                       # uuid hex
    dedup_key: str                # hash of canonical URL — uniqueness across runs
    source: str = "apify-linkedin"
    source_id: Optional[str] = None
    company: str = ""
    title: str = ""
    location: str = ""
    posted_at: Optional[str] = None    # ISO; may be coarse ("3 days ago" → resolved relative to discovery time)
    salary: Optional[str] = None       # free-text from posting
    description: str = ""              # full JD text (truncated to ~6k chars for storage sanity)
    application_link: str = ""         # canonical URL
    # Derived signals
    score: int = 0                     # 0-100 fit score
    score_breakdown: dict = Field(default_factory=dict)
    sponsorship_status: str = "unspecified"   # mirrors SponsorshipInfo.status
    languages_required: List[str] = Field(default_factory=list)   # languages from extractor (required ones)
    yoe_required: Optional[int] = None
    # User actions
    rejected: bool = False             # filtered out by hard rules
    rejection_reason: str = ""
    applied: bool = False
    applied_id: Optional[str] = None
    generation_id: Optional[str] = None
    # Bookkeeping
    discovered_at: str = ""            # ISO
    last_seen_at: str = ""             # bumped each run we re-encounter it


class DiscoveryRun(BaseModel):
    id: str
    started_at: str
    finished_at: Optional[str] = None
    source: str = "apify-linkedin"
    raw_count: int = 0
    added: int = 0
    dup_skipped: int = 0
    rejected: int = 0
    autogen_count: int = 0
    error: Optional[str] = None

