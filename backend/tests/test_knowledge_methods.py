from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.api.routes import router
from app.core.db import Base, get_db
from app.models import KnowledgeDerivation, KnowledgeDocument
from app.services.knowledge_methods import (
    FAULT_CASE_TEMPLATE,
    extract_analysis_method,
    parse_markdown_sections,
)
from app.services.knowledge_taxonomy import seed_knowledge_categories
from app.services.model_profiles import seed_model_profiles


def _completed_fault_case(solution: str = "升级认证模块并重启无线服务。") -> str:
    return f"""# 故障案例：AP 认证超时

## 错误形式

- 用户可见现象：终端连接 AP 时认证超时。

## 日志分析

- 关键日志模式：EAP timeout 出现在四次握手之前。

## 错误定位

1. 对比正常样本，定位到 auth_retry 函数未重置计时器。

## 解决方案

1. {solution}

## 验证结果

- 连续认证 100 次均成功。
"""


def test_fault_case_template_requires_substantive_section_content() -> None:
    blank = parse_markdown_sections(FAULT_CASE_TEMPLATE)
    assert blank["complete"] is False
    assert set(blank["missing_sections"]) == {
        "error_form", "log_analysis", "localization", "solution",
    }

    completed = parse_markdown_sections(_completed_fault_case())
    assert completed["complete"] is True
    assert completed["completeness"] == 1.0
    assert "EAP timeout" in completed["sections"]["log_analysis"]

    heading_only = parse_markdown_sections(
        "# 错误形式\n认证失败\n# 日志分析\nEAP timeout\n"
        "# 错误定位\n定位到计时器\n# 解决方案\n重置计时器\n"
    )
    assert heading_only["complete"] is True

    fenced_heading = parse_markdown_sections(
        "# 故障案例\n"
        "## 错误形式\n认证失败\n"
        "## 日志分析\n```text\n## 这是一行日志，不是 Markdown 章节\n```\n"
        "关键日志为 EAP timeout\n"
        "## 错误定位\n定位到计时器\n"
        "## 解决方案\n重置计时器\n"
    )
    assert fenced_heading["complete"] is True
    assert "这是一行日志" in fenced_heading["sections"]["log_analysis"]
    assert all(
        section["heading"] != "这是一行日志，不是 Markdown 章节"
        for section in fenced_heading["other_sections"]
    )

    skill = KnowledgeDocument(
        id="DOC-skill",
        title="认证错误分析 Skill",
        source_type="analysis_skill",
        content=(
            "# 认证错误分析 Skill\n"
            "## 适用场景\n认证超时\n"
            "## 证据收集\n对齐 EAP 日志\n"
            "## 分析步骤\n如果 EAP 成功，则检查四次握手\n"
            "## 验证方法\n执行连续认证回归\n"
        ),
    )
    method, metadata = extract_analysis_method(skill)
    assert "检查四次握手" in method
    assert "分支决策点" in method
    assert metadata["extraction_mode"] == "deterministic_markdown_v1"


def test_fault_case_api_extracts_updates_and_cascades_analysis_method(
    tmp_path: Path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'knowledge-methods.db'}",
        connect_args={"check_same_thread": False},
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with session_factory() as db:
        seed_knowledge_categories(db)
        seed_model_profiles(db)

    def override_db():
        with session_factory() as db:
            yield db

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_db

    with TestClient(app) as client:
        template = client.get("/api/v1/knowledge/templates/fault-case")
        assert template.status_code == 200
        assert template.json()["source_type"] == "fault_case"

        created = client.post("/api/v1/knowledge", json={
            "title": "AP 认证超时案例",
            "source_type": "fault_case",
            "content": _completed_fault_case(),
            "device_type": "AP",
            "module": "WLAN",
            "trust_level": "HIGH",
        })
        assert created.status_code == 200, created.text
        source = created.json()
        assert source["category_name"] == "结构化故障案例"
        assert source["metadata"]["markdown_structure"]["complete"] is True

        extracted = client.post(
            f"/api/v1/knowledge/{source['id']}/extract-method"
        )
        assert extracted.status_code == 200, extracted.text
        extracted_payload = extracted.json()
        assert extracted_payload["created"] is True
        derived = extracted_payload["derived_document"]
        assert derived["source_type"] == "analysis_method"
        assert derived["category_name"] == "提炼分析方法"
        assert "只重组原文" in derived["content"]
        assert "auth_retry" in derived["content"]

        updated = client.patch(f"/api/v1/knowledge/{source['id']}", json={
            "content": _completed_fault_case("回退错误 Commit 并增加计时器单元测试。"),
        })
        assert updated.status_code == 200, updated.text
        refreshed_method = client.get(f"/api/v1/knowledge/{derived['id']}")
        assert refreshed_method.status_code == 200
        assert "回退错误 Commit" in refreshed_method.json()["content"]

        manually_edited = client.patch(f"/api/v1/knowledge/{derived['id']}", json={
            "content": "# 人工复核方法\n\n保留工程师补充的专用检查步骤。",
        })
        assert manually_edited.status_code == 200
        source_changed_again = client.patch(
            f"/api/v1/knowledge/{source['id']}",
            json={"content": _completed_fault_case("升级到新的认证模块版本。")},
        )
        assert source_changed_again.status_code == 200
        preserved = client.get(f"/api/v1/knowledge/{derived['id']}").json()
        assert "工程师补充" in preserved["content"]
        assert (
            preserved["metadata"]["derivation_status"]
            == "SOURCE_UPDATED_DERIVED_MANUALLY_EDITED"
        )

        regenerated = client.post(
            f"/api/v1/knowledge/{source['id']}/extract-method"
        )
        assert regenerated.status_code == 200
        assert regenerated.json()["created"] is False
        assert "升级到新的认证模块版本" in (
            regenerated.json()["derived_document"]["content"]
        )

        deleted = client.delete(f"/api/v1/knowledge/{source['id']}")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["deleted_derived_documents"] == [derived["id"]]
        assert client.get(f"/api/v1/knowledge/{derived['id']}").status_code == 404

    with session_factory() as db:
        assert db.scalar(select(func.count(KnowledgeDerivation.id))) == 0
        assert db.scalar(select(func.count(KnowledgeDocument.id))) == 0
    engine.dispose()
