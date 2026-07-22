from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.utils import new_id
from app.models import KnowledgeCategory, KnowledgeDocument, KnowledgeDocumentCategory


DEFAULT_CATEGORIES = [
    ("KCAT-diagnosis", "诊断规则", "diagnosis", None, 10, "用于确定性诊断与证据匹配的规则"),
    ("KCAT-diagnosis-log", "日志与错误码规则", "diagnosis.log_rules", "KCAT-diagnosis", 10, "日志模式、错误码和事件规则"),
    ("KCAT-diagnosis-protocol", "协议诊断规则", "diagnosis.protocol_rules", "KCAT-diagnosis", 20, "WLAN、WAN、PON、OMCI 等协议规则"),
    ("KCAT-diagnosis-product", "产品诊断规则", "diagnosis.product_rules", "KCAT-diagnosis", 30, "设备型号和版本相关规则"),
    ("KCAT-diagnosis-security", "安全诊断规则", "diagnosis.security_rules", "KCAT-diagnosis", 40, "认证、凭据和安全异常规则"),
    ("KCAT-history", "历史问题诊断", "history", None, 20, "历史故障、故障树和解决方案"),
    ("KCAT-history-fault-tree", "故障树", "history.fault_trees", "KCAT-history", 10, "从现象到根因的分支诊断过程"),
    ("KCAT-history-solutions", "解决方案", "history.solutions", "KCAT-history", 20, "已验证修复方法、验证步骤和回退方案"),
    ("KCAT-history-known", "已知问题与案例", "history.known_issues", "KCAT-history", 30, "历史问题单和相似案例"),
    ("KCAT-reference", "参考资料", "reference", None, 30, "参与检索但不直接作为确定性规则的资料"),
    ("KCAT-reference-product", "产品文档", "reference.product_docs", "KCAT-reference", 10, "产品说明和版本资料"),
    ("KCAT-reference-protocol", "协议文档", "reference.protocol_docs", "KCAT-reference", 20, "标准和协议说明"),
    ("KCAT-reference-test", "测试规范", "reference.test_specs", "KCAT-reference", 30, "测试流程、验收标准和复现步骤"),
]


SOURCE_TYPE_DEFAULT_CATEGORY = {
    "builtin_rule": "diagnosis.log_rules",
    "log_rule": "diagnosis.log_rules",
    "diagnostic_rule": "diagnosis.product_rules",
    "protocol_rule": "diagnosis.protocol_rules",
    "security_rule": "diagnosis.security_rules",
    "fault_tree": "history.fault_trees",
    "solution": "history.solutions",
    "historical_bug": "history.known_issues",
    "known_issue": "history.known_issues",
    "protocol": "reference.protocol_docs",
    "test_spec": "reference.test_specs",
    "document": "reference.product_docs",
}


def seed_knowledge_categories(db: Session) -> None:
    for category_id, name, code, parent_id, sort_order, description in DEFAULT_CATEGORIES:
        if not db.get(KnowledgeCategory, category_id):
            db.add(KnowledgeCategory(
                id=category_id,
                name=name,
                code=code,
                parent_id=parent_id,
                sort_order=sort_order,
                description=description,
                system=True,
            ))
    db.commit()


def get_default_category_id(db: Session, source_type: str) -> str | None:
    code = SOURCE_TYPE_DEFAULT_CATEGORY.get(source_type, "reference.product_docs")
    return db.scalar(select(KnowledgeCategory.id).where(KnowledgeCategory.code == code))


def set_document_category(db: Session, document_id: str, category_id: str | None) -> None:
    link = db.get(KnowledgeDocumentCategory, document_id)
    if category_id is None:
        if link:
            db.delete(link)
        return
    category = db.get(KnowledgeCategory, category_id)
    if not category or not category.active:
        raise ValueError("Knowledge category not found or inactive")
    if link:
        link.category_id = category_id
    else:
        db.add(KnowledgeDocumentCategory(document_id=document_id, category_id=category_id))


def assign_uncategorized_documents(db: Session) -> int:
    linked_ids = select(KnowledgeDocumentCategory.document_id)
    documents = list(db.scalars(
        select(KnowledgeDocument).where(KnowledgeDocument.id.not_in(linked_ids))
    ).all())
    for document in documents:
        set_document_category(db, document.id, get_default_category_id(db, document.source_type))
    db.commit()
    return len(documents)


def descendant_category_ids(db: Session, category_id: str) -> set[str]:
    rows = list(db.scalars(select(KnowledgeCategory)).all())
    children: dict[str, list[str]] = {}
    for row in rows:
        if row.parent_id:
            children.setdefault(row.parent_id, []).append(row.id)
    result = {category_id}
    pending = [category_id]
    while pending:
        current = pending.pop()
        for child_id in children.get(current, []):
            if child_id not in result:
                result.add(child_id)
                pending.append(child_id)
    return result


def validate_category_parent(db: Session, category: KnowledgeCategory, parent_id: str | None) -> None:
    if parent_id is None:
        return
    if parent_id == category.id:
        raise ValueError("A category cannot be its own parent")
    parent = db.get(KnowledgeCategory, parent_id)
    if not parent:
        raise ValueError("Parent category not found")
    if parent_id in descendant_category_ids(db, category.id):
        raise ValueError("Category hierarchy would contain a cycle")


def category_document_counts(db: Session) -> Counter[str]:
    return Counter(db.scalars(select(KnowledgeDocumentCategory.category_id)).all())


def new_category_code(category_id: str) -> str:
    return f"custom.{category_id.lower()}"


def new_category_id() -> str:
    return new_id("KCAT")
