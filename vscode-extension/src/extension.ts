import * as vscode from 'vscode'
import axios from 'axios'
import FormData from 'form-data'
import archiver from 'archiver'
import * as fs from 'fs'
import * as os from 'os'
import * as path from 'path'

function workspaceConfig() {
  const c = vscode.workspace.getConfiguration('gwap')
  return {
    backend: (c.get<string>('backendUrl') || 'http://127.0.0.1:8000/api/v1').replace(/\/$/, ''),
    web: (c.get<string>('webUrl') || 'http://127.0.0.1:5173').replace(/\/$/, ''),
    defaultCaseId: c.get<string>('defaultCaseId') || ''
  }
}

async function config(context: vscode.ExtensionContext) {
  const c = workspaceConfig()
  const legacyApiKey = vscode.workspace.getConfiguration('gwap').get<string>('apiKey') || ''
  const apiKey = (await context.secrets.get('gwap.apiKey')) || legacyApiKey
  return { ...c, headers: apiKey ? { 'X-API-Key': apiKey } : {} }
}

async function caseIdPrompt(): Promise<string | undefined> {
  const value = await vscode.window.showInputBox({
    prompt: 'GW/AP Debug Case ID',
    value: workspaceConfig().defaultCaseId
  })
  return value?.trim() || undefined
}

async function uploadFile(
  context: vscode.ExtensionContext,
  caseId: string,
  uri: vscode.Uri,
  endpoint: string,
  field = 'file'
) {
  const form = new FormData()
  form.append(field, fs.createReadStream(uri.fsPath), path.basename(uri.fsPath))
  const c = await config(context)
  return axios.post(`${c.backend}${endpoint}`, form, { headers: { ...form.getHeaders(), ...c.headers }, maxBodyLength: Infinity, timeout: 300000 })
}

async function zipWorkspace(root: string): Promise<string> {
  const target = path.join(os.tmpdir(), `gwap-workspace-${Date.now()}.zip`)
  await new Promise<void>((resolve, reject) => {
    const output = fs.createWriteStream(target)
    const archive = archiver('zip', { zlib: { level: 6 } })
    output.on('close', resolve)
    archive.on('error', reject)
    archive.pipe(output)
    archive.glob('**/*', {
      cwd: root,
      dot: true,
      ignore: [
        '.git/**', 'node_modules/**', 'build/**', 'dist/**', '.venv/**', '*.zip',
        '.env', '.env.*', '**/.env', '**/.env.*', '*.pem', '*.key', '*.p12', '*.pfx',
        '**/id_rsa*', '**/id_ed25519*'
      ]
    })
    archive.finalize()
  })
  return target
}

export function activate(context: vscode.ExtensionContext) {
  context.subscriptions.push(vscode.commands.registerCommand('gwap.setCredential', async () => {
    const value = await vscode.window.showInputBox({
      prompt: 'Paste an API key or gwdp_ personal token. Submit an empty value to clear it.',
      placeHolder: 'gwdp_...',
      password: true,
      ignoreFocusOut: true
    })
    if (value === undefined) return
    const credential = value.trim()
    if (credential) {
      await context.secrets.store('gwap.apiKey', credential)
      vscode.window.showInformationMessage('GW/AP credential saved in VS Code SecretStorage.')
    } else {
      await context.secrets.delete('gwap.apiKey')
      vscode.window.showInformationMessage('GW/AP SecretStorage credential cleared.')
    }
  }))

  context.subscriptions.push(vscode.commands.registerCommand('gwap.createCase', async () => {
    const title = await vscode.window.showInputBox({ prompt: 'Problem title' })
    if (!title) return
    const deviceType = await vscode.window.showQuickPick(['GW', 'AP', 'OTHER'], { placeHolder: 'Device type' })
    if (!deviceType) return
    const description = await vscode.window.showInputBox({ prompt: 'Problem symptom' }) || ''
    const c = await config(context)
    const response = await axios.post(`${c.backend}/cases`, { title, device_type: deviceType, description }, { headers: c.headers })
    await vscode.workspace.getConfiguration('gwap').update('defaultCaseId', response.data.id, vscode.ConfigurationTarget.Workspace)
    vscode.window.showInformationMessage(`Created ${response.data.id}`)
  }))

  context.subscriptions.push(vscode.commands.registerCommand('gwap.uploadDebugInfo', async () => {
    const caseId = await caseIdPrompt(); if (!caseId) return
    const picked = await vscode.window.showOpenDialog({
      canSelectMany: false,
      filters: {
        'Debug info': ['zip', 'tar', 'gz', 'tgz', 'log', 'txt'],
        'All files (including extensionless logs)': ['*']
      }
    })
    if (!picked?.[0]) return
    const response = await uploadFile(context, caseId, picked[0], `/cases/${caseId}/artifacts`)
    const c = await config(context)
    const parse = await axios.post(`${c.backend}/cases/${caseId}/artifacts/${response.data.id}/parse`, {}, { headers: c.headers })
    vscode.window.showInformationMessage(`Uploaded. Parse job: ${parse.data.id}`)
  }))

  context.subscriptions.push(vscode.commands.registerCommand('gwap.uploadWorkspace', async () => {
    const caseId = await caseIdPrompt(); if (!caseId) return
    const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath
    if (!root) return vscode.window.showWarningMessage('Open a workspace first')
    await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: 'Packaging and uploading workspace', cancellable: false }, async () => {
      const zip = await zipWorkspace(root)
      try {
        const response = await uploadFile(context, caseId, vscode.Uri.file(zip), `/cases/${caseId}/repositories`)
        const c = await config(context)
        const job = await axios.post(`${c.backend}/repositories/${response.data.repository_id}/index`, {}, { headers: c.headers })
        vscode.window.showInformationMessage(`Repository uploaded. Index job: ${job.data.id}`)
      } finally { fs.rmSync(zip, { force: true }) }
    })
  }))

  context.subscriptions.push(vscode.commands.registerCommand('gwap.askSelection', async () => {
    const caseId = await caseIdPrompt(); if (!caseId) return
    const editor = vscode.window.activeTextEditor
    if (!editor) return
    const selected = editor.document.getText(editor.selection) || editor.document.getText().slice(0, 6000)
    const question = await vscode.window.showInputBox({ prompt: 'Question about selected code', value: '结合当前故障，分析这段代码是否相关' })
    if (!question) return
    const c = await config(context)
    const payload = { question: `${question}\n文件：${vscode.workspace.asRelativePath(editor.document.uri)}\n代码：\n${selected}` }
    const response = await axios.post(`${c.backend}/cases/${caseId}/chat`, payload, { headers: c.headers, timeout: 180000 })
    const doc = await vscode.workspace.openTextDocument({ language: 'markdown', content: `# GW/AP Debug Answer\n\n${response.data.answer}\n\n## Citations\n${response.data.citations.map((x:any) => `- ${x.evidence_id}: ${x.title || x.source_file || x.source_type}`).join('\n')}` })
    await vscode.window.showTextDocument(doc, { preview: false })
  }))

  context.subscriptions.push(vscode.commands.registerCommand('gwap.openCase', async () => {
    const caseId = await caseIdPrompt(); if (!caseId) return
    await vscode.env.openExternal(vscode.Uri.parse(`${workspaceConfig().web}/cases/${caseId}`))
  }))
}

export function deactivate() {}
