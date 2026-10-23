from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class QueryIntent(StrEnum):
    PRODUCT = "product"
    ORDER_CARE = "order_care"
    CONVERSATION = "conversation"


class ResponseFocus(StrEnum):
    SUMMARY = "summary"
    ADVANTAGES = "advantages"
    PRICE = "price"
    RATING = "rating"
    STOCK = "stock"
    COMPARISON = "comparison"


class QueryConstraints(BaseModel):
    max_price_cents: int | None = Field(default=None, ge=0)
    preferences: list[str] = Field(default_factory=list, max_length=20)
    excluded_terms: list[str] = Field(default_factory=list, max_length=20)


class QueryUnderstanding(BaseModel):
    intents: list[QueryIntent] = Field(min_length=1, max_length=3)
    focus: ResponseFocus = ResponseFocus.SUMMARY
    constraints: QueryConstraints = Field(default_factory=QueryConstraints)
    referenced_products: list[str] = Field(default_factory=list, max_length=3)
    confidence: float = Field(ge=0, le=1)
    needs_clarification: bool = False
    clarification_question: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def require_clarification_question(self) -> "QueryUnderstanding":
        if self.needs_clarification and not self.clarification_question:
            raise ValueError("clarification_question is required when clarification is needed")
        return self


FOCUS_TERMS: tuple[tuple[ResponseFocus, tuple[str, ...]], ...] = (
    (
        ResponseFocus.ADVANTAGES,
        (
            "优势",
            "优点",
            "特点",
            "亮点",
            "卖点",
            "长处",
            "强项",
            "好在哪",
            "值得买",
            "适合吗",
            "怎么样",
        ),
    ),
    (ResponseFocus.PRICE, ("价格", "多少钱", "售价", "贵吗", "便宜", "价位")),
    (ResponseFocus.RATING, ("评分", "评价", "口碑", "评论")),
    (ResponseFocus.STOCK, ("库存", "库寸", "有货", "现货", "还有吗", "能下单")),
    (
        ResponseFocus.COMPARISON,
        ("对比", "比较", "区别", "差别", "哪个更", "哪一个更", "怎么选"),
    ),
)


def detect_response_focus(text: str) -> ResponseFocus:
    normalized = "".join(text.casefold().split())
    for focus, terms in FOCUS_TERMS:
        if any(term in normalized for term in terms):
            return focus
    return ResponseFocus.SUMMARY
