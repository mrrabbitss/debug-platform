"""Synthetic knowledge correction -> retrieval -> real Codex answer evaluation.

This is a narrow answer-quality smoke, not full interactive MCP acceptance.
Never reads company cases, model keys or user CLI configuration. Uses existing
Codex authentication and sends only generated synthetic questions and evidence.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


def evaluate(codex: Path, output: Path) -> dict:
    started = time.monotonic()
    summary = {'status': 'FAIL', 'scope': 'synthetic_corrected_knowledge_real_cli_answer',
               'full_mcp_workflow': False, 'company_data_used': False, 'backend_chat_calls': 0}
    with tempfile.TemporaryDirectory(prefix='gwap-cli-learning-') as temporary:
        work = Path(temporary)
        os.environ.update(DATABASE_URL=f'sqlite:///{work / "eval.db"}', STORAGE_ROOT=str(work / 'storage'),
                          LLM_PROVIDER='mock', EMBEDDING_PROVIDER='hashing', RERANKER_PROVIDER='disabled')
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.core.db import Base, configure_sqlite_engine
        from app.core.utils import json_loads
        from app.models import KnowledgeDocument
        from app.services.knowledge import index_document
        from app.services.knowledge_drafts import save_draft
        from app.services.knowledge_personal import personal_view
        from app.services.rag import retriever

        engine = create_engine(os.environ['DATABASE_URL'])
        configure_sqlite_engine(engine)
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        try:
            with factory() as db:
                doc = KnowledgeDocument(id='DOC-synthetic-correction', title='AP heartbeat timeout troubleshooting',
                    source_type='analysis_skill', active=True, review_status='ACTIVE', content=(
                        '# Obsolete diagnosis\nAP heartbeat timeout was previously attributed to authentication.'))
                db.add(doc)
                db.flush()
                index_document(db, doc)
                save_draft(db, doc, {'content': (
                    '# Human corrected diagnostic method\n'
                    'AP heartbeat timeout is a symptom, not a cause.\n'
                    'A POWER_BROWNOUT event followed by an uptime reset and then a heartbeat timeout indicates power failure.\n'
                    'An AUTH_REJECT event with a mismatched key_hash indicates an authentication key mismatch instead.\n'
                    'Heartbeat timeout alone cannot distinguish those causes. Require the preceding events before concluding.\n')},
                    expected_lock_version=doc.lock_version, expected_draft_version=None, author='synthetic-engineer')
                view = personal_view(db, 'synthetic-engineer')
                db.commit()
                questions = [
                    ('same', 'AP heartbeat timeout: POWER_BROWNOUT then uptime reset then heartbeat timeout.', 'POWER'),
                    ('similar', 'An access point disappears repeatedly. Logs show POWER_BROWNOUT, its running-time counter returns to zero, then the GW reports missed heartbeats.', 'POWER'),
                    ('different', 'An AP cannot connect. AUTH_REJECT and mismatched key_hash appear, but uptime is steady and no brownout is recorded.', 'AUTHENTICATION'),
                    ('insufficient', 'An AP has a heartbeat timeout. There are no preceding event records available.', 'INSUFFICIENT'),
                ]
                inputs = []
                for identifier, question, expected in questions:
                    hits = retriever.search(question, db=db, apply_models=False, knowledge_view=view, top_k=3)
                    if not hits or any(hit.metadata.get('personal_revision_id') not in view for hit in hits):
                        raise ValueError('Corrected knowledge was not retrieved')
                    inputs.append({'id': identifier, 'question': question, 'evidence': [
                        {'id': hit.evidence_id, 'text': hit.content} for hit in hits]})
                summary['personal_revision_pinned'] = bool(view)
                summary['published_content_preserved'] = 'Obsolete' in db.get(KnowledgeDocument, doc.id).content
                summary['snapshot_retained'] = bool(json_loads(db.get(KnowledgeDocument, doc.id).metadata_json, {}))
        finally:
            engine.dispose()
        schema = {'type': 'object', 'additionalProperties': False, 'required': ['answers'], 'properties': {
            'answers': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
                'required': ['id', 'cause', 'evidence_ids'], 'properties': {
                    'id': {'type': 'string'}, 'cause': {'type': 'string', 'enum': ['POWER', 'AUTHENTICATION', 'INSUFFICIENT']},
                    'evidence_ids': {'type': 'array', 'items': {'type': 'string'}}}}}}}
        schema_path, answer_path = work / 'schema.json', work / 'answer.json'
        schema_path.write_text(json.dumps(schema), encoding='utf-8')
        prompt = ('This is a synthetic closed-book answer evaluation. Do not call tools, search, read files or execute commands. '
                  'Use only each supplied question and its retrieved evidence. Historical methods are guidance, not proof of current facts. '
                  'For each question select POWER, AUTHENTICATION or INSUFFICIENT and cite only its supplied evidence IDs. '
                  'Return the specified JSON. Inputs: ' + json.dumps(inputs))
        completed = subprocess.run([str(codex), 'exec', '--ignore-user-config', '--ephemeral', '--skip-git-repo-check',
            '-C', str(work), '-s', 'read-only', '--output-schema', str(schema_path), '-o', str(answer_path), '-'],
            input=prompt, text=True, encoding='utf-8', errors='replace', capture_output=True, timeout=240,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        summary['cli_exit_code'] = completed.returncode
        if completed.returncode or not answer_path.is_file():
            summary['error'] = 'Codex CLI did not complete; no credentials or model output retained'
        else:
            answers = {row['id']: row for row in json.loads(answer_path.read_text(encoding='utf-8'))['answers']}
            checks = []
            for (identifier, _, expected), supplied in zip(questions, inputs, strict=True):
                answer = answers.get(identifier, {})
                citations = set(answer.get('evidence_ids', []))
                checks.append({'scenario': identifier, 'cause_correct': answer.get('cause') == expected,
                               'citations_valid': bool(citations) and citations <= {row['id'] for row in supplied['evidence']}})
            summary.update(checks=checks, status='PASS' if all(c['cause_correct'] and c['citations_valid'] for c in checks) else 'FAIL')
    summary['duration_seconds'] = round(time.monotonic() - started, 3)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2), encoding='utf-8')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = evaluate(args.codex, args.output)
    except Exception as error:
        result = {'status': 'FAIL', 'error_type': type(error).__name__, 'company_data_used': False}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['status'] == 'PASS' else 1)
