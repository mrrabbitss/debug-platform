import type { KnowledgeCurationSession, KnowledgeCurationSource } from '../types'

export function useKnowledgeCurationPresentation() {
  function formatBytes(value: number) {
    if (value < 1024) return `${value} B`
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`
    return `${(value / 1024 / 1024).toFixed(2)} MiB`
  }

  function statusLabel(status: KnowledgeCurationSession['status']) {
    return {
      QUEUED: '等待提炼',
      EXTRACTING: '模型提炼中',
      REVIEWING: '人工校正中',
      FAILED: '提炼失败',
      CANCELLED: '已取消',
      CONFIRMING: '正在加入知识库',
      CONFIRMED: '已生成知识草稿'
    }[status] || status
  }

  function statusType(status: KnowledgeCurationSession['status']) {
    if (status === 'CONFIRMED') return 'success'
    if (status === 'FAILED') return 'danger'
    if (status === 'REVIEWING') return 'warning'
    return 'info'
  }

  function sourceRoleLabel(role: KnowledgeCurationSource['source_role']) {
    return {
      log: '日志',
      error: '错误情况',
      analysis: '分析',
      solution: '解决方案',
      context: '上下文'
    }[role] || role
  }

  function extractionMethodLabel(source: KnowledgeCurationSource) {
    const labels: Record<string, string> = {
      plain_text: '纯文本',
      html_visible_text: 'HTML 正文',
      docx_paragraphs_tables: 'Word DOCX',
      pdf_text_layer: 'PDF 文本层'
    }
    const label = labels[source.extraction_method || ''] || '待检测'
    return source.page_count ? `${label} · ${source.page_count} 页` : label
  }

  function skipReasonLabel(reason?: string) {
    const labels: Record<string, string> = {
      binary_or_unsupported_text_encoding: '二进制或无法识别的文本编码',
      empty_text_file: '空文本文件',
      prompt_budget_exhausted: '模型证据总长度已达上限',
      legacy_doc_requires_conversion: '旧式 .doc 需先转换为 .docx',
      invalid_docx: '不是有效的 DOCX 文件',
      encrypted_docx: '不支持加密 DOCX',
      docx_too_many_parts: 'DOCX 内部文件过多',
      docx_part_too_large: 'DOCX 内部文件过大',
      docx_uncompressed_too_large: 'DOCX 解压后超过安全限制',
      docx_extraction_failed: 'DOCX 正文提取失败',
      html_not_readable_text: 'HTML 编码无法识别',
      html_extraction_failed: 'HTML 正文提取失败',
      encrypted_pdf: '不支持加密 PDF',
      invalid_pdf: 'PDF 文件无效或损坏',
      document_has_no_extractable_text: '没有可提取正文；扫描 PDF 需要 OCR'
    }
    return labels[reason || ''] || reason || '未纳入模型证据'
  }

  return {
    extractionMethodLabel,
    formatBytes,
    skipReasonLabel,
    sourceRoleLabel,
    statusLabel,
    statusType
  }
}
