from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CompanyErrorRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int | None
    company_name: str | None = None
    company_uid: str | None = None
    error_source: str
    error_type: str
    message: str | None
    detail_json: str | None
    job_run_id: int | None
    created_at: datetime
    resolved_at: datetime | None
    ignored: bool


class CompanyErrorPage(BaseModel):
    items: list[CompanyErrorRead]
    total: int
    page: int
    page_size: int


class CompanyCorrection(BaseModel):
    """Fields the admin can override inline in the Error Center."""
    website_url: str | None = None
    purpose: str | None = None
    noga_code: str | None = None
    noga_label: str | None = None
    address_city: str | None = None
    address_zip: str | None = None
    ai_category: str | None = None
    ai_score: int | None = None
