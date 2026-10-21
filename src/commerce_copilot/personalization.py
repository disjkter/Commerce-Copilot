from .contracts import AgentRole, Mission, UserPreferences


PRODUCT_CATEGORY_TERMS = (
    "耳机",
    "键盘",
    "鼠标",
    "电脑",
    "手机",
    "音箱",
)

GENERIC_PRODUCT_REQUESTS = {
    "推荐商品",
    "推荐产品",
    "推荐一下",
    "有什么推荐",
    "我想买东西",
    "买东西",
}


def needs_product_clarification(query: str) -> bool:
    normalized = "".join(query.strip().split()).rstrip("？?。！!")
    return normalized in GENERIC_PRODUCT_REQUESTS


def extract_product_categories(query: str) -> list[str]:
    return [term for term in PRODUCT_CATEGORY_TERMS if term in query]


def personalize_mission(mission: Mission, preferences: UserPreferences) -> Mission:
    """Use saved preferences only where the current request has no explicit value."""
    if mission.role != AgentRole.PRODUCT_SCOUT:
        return mission

    parameters = dict(mission.parameters)
    if parameters.get("selected_references"):
        return mission.model_copy(update={"parameters": parameters})

    applied: dict[str, object] = {}
    query = str(parameters.get("product_query", mission.instruction)).strip()
    explicit_categories = extract_product_categories(query)
    if explicit_categories:
        parameters["categories"] = explicit_categories

    if parameters.get("max_price_cents") is None and preferences.max_budget_cents:
        parameters["max_price_cents"] = preferences.max_budget_cents
        applied["max_budget_cents"] = preferences.max_budget_cents

    additions: list[str] = []
    if not explicit_categories:
        additions.extend(preferences.preferred_categories)
        if preferences.preferred_categories:
            parameters["categories"] = preferences.preferred_categories
            applied["preferred_categories"] = preferences.preferred_categories

    for field_name, terms in (
        ("preferred_brands", preferences.preferred_brands),
        ("scenarios", preferences.scenarios),
        ("priorities", preferences.priorities),
    ):
        missing_terms = [term for term in terms if term not in query]
        additions.extend(missing_terms)
        if missing_terms:
            applied[field_name] = missing_terms

    if additions:
        query = " ".join(dict.fromkeys([query, *additions]))
        parameters["product_query"] = query
    parameters["applied_preferences"] = applied
    return mission.model_copy(update={"parameters": parameters})
