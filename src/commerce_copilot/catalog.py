from pydantic import BaseModel, Field


class Product(BaseModel):
    product_id: str
    title: str
    category: str
    brand: str
    description: str
    price_cents: int = Field(ge=0)
    rating: float = Field(ge=0, le=5)
    review_count: int = Field(ge=0)
    stock: int = Field(ge=0)
    tags: list[str] = Field(default_factory=list)


class ProductSearchHit(BaseModel):
    product: Product
    lexical_score: float
