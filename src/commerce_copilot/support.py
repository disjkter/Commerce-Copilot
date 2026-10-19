from pydantic import BaseModel, Field


class OrderItem(BaseModel):
    product_id: str
    title: str
    quantity: int = Field(gt=0)
    unit_price_cents: int = Field(ge=0)


class OrderRecord(BaseModel):
    order_id: str
    status: str
    placed_at: str
    items: list[OrderItem] = Field(default_factory=list)


class PolicyRecord(BaseModel):
    topic: str
    title: str
    body: str
    effective_from: str
