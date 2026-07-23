"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.activate = activate;
exports.deactivate = deactivate;
const vscode = __importStar(require("vscode"));
const axios_1 = __importDefault(require("axios"));
const form_data_1 = __importDefault(require("form-data"));
const archiver_1 = __importDefault(require("archiver"));
const fs = __importStar(require("fs"));
const os = __importStar(require("os"));
const path = __importStar(require("path"));
function workspaceConfig() {
    const c = vscode.workspace.getConfiguration('gwap');
    return {
        backend: (c.get('backendUrl') || 'http://127.0.0.1:8000/api/v1').replace(/\/$/, ''),
        web: (c.get('webUrl') || 'http://127.0.0.1:5173').replace(/\/$/, ''),
        defaultCaseId: c.get('defaultCaseId') || ''
    };
}
async function config(context) {
    const c = workspaceConfig();
    const legacyApiKey = vscode.workspace.getConfiguration('gwap').get('apiKey') || '';
    const apiKey = (await context.secrets.get('gwap.apiKey')) || legacyApiKey;
    return { ...c, headers: apiKey ? { 'X-API-Key': apiKey } : {} };
}
async function caseIdPrompt() {
    const value = await vscode.window.showInputBox({
        prompt: 'GW/AP Debug Case ID',
        value: workspaceConfig().defaultCaseId
    });
    return value?.trim() || undefined;
}
async function uploadFile(context, caseId, uri, endpoint, field = 'file') {
    const form = new form_data_1.default();
    form.append(field, fs.createReadStream(uri.fsPath), path.basename(uri.fsPath));
    const c = await config(context);
    return axios_1.default.post(`${c.backend}${endpoint}`, form, { headers: { ...form.getHeaders(), ...c.headers }, maxBodyLength: Infinity, timeout: 300000 });
}
async function zipWorkspace(root) {
    const target = path.join(os.tmpdir(), `gwap-workspace-${Date.now()}.zip`);
    await new Promise((resolve, reject) => {
        const output = fs.createWriteStream(target);
        const archive = (0, archiver_1.default)('zip', { zlib: { level: 6 } });
        output.on('close', resolve);
        archive.on('error', reject);
        archive.pipe(output);
        archive.glob('**/*', {
            cwd: root,
            dot: true,
            ignore: [
                '.git/**', 'node_modules/**', 'build/**', 'dist/**', '.venv/**', '*.zip',
                '.env', '.env.*', '**/.env', '**/.env.*', '*.pem', '*.key', '*.p12', '*.pfx',
                '**/id_rsa*', '**/id_ed25519*'
            ]
        });
        archive.finalize();
    });
    return target;
}
function activate(context) {
    context.subscriptions.push(vscode.commands.registerCommand('gwap.setCredential', async () => {
        const value = await vscode.window.showInputBox({
            prompt: 'Paste an API key or gwdp_ personal token. Submit an empty value to clear it.',
            placeHolder: 'gwdp_...',
            password: true,
            ignoreFocusOut: true
        });
        if (value === undefined)
            return;
        const credential = value.trim();
        if (credential) {
            await context.secrets.store('gwap.apiKey', credential);
            vscode.window.showInformationMessage('GW/AP credential saved in VS Code SecretStorage.');
        }
        else {
            await context.secrets.delete('gwap.apiKey');
            vscode.window.showInformationMessage('GW/AP SecretStorage credential cleared.');
        }
    }));
    context.subscriptions.push(vscode.commands.registerCommand('gwap.createCase', async () => {
        const title = await vscode.window.showInputBox({ prompt: 'Problem title' });
        if (!title)
            return;
        const deviceType = await vscode.window.showQuickPick(['GW', 'AP', 'OTHER'], { placeHolder: 'Device type' });
        if (!deviceType)
            return;
        const description = await vscode.window.showInputBox({ prompt: 'Problem symptom' }) || '';
        const c = await config(context);
        const response = await axios_1.default.post(`${c.backend}/cases`, { title, device_type: deviceType, description }, { headers: c.headers });
        await vscode.workspace.getConfiguration('gwap').update('defaultCaseId', response.data.id, vscode.ConfigurationTarget.Workspace);
        vscode.window.showInformationMessage(`Created ${response.data.id}`);
    }));
    context.subscriptions.push(vscode.commands.registerCommand('gwap.uploadDebugInfo', async () => {
        const caseId = await caseIdPrompt();
        if (!caseId)
            return;
        const picked = await vscode.window.showOpenDialog({
            canSelectMany: false,
            filters: {
                'Debug info': ['zip', 'tar', 'gz', 'tgz', 'log', 'txt'],
                'All files (including extensionless logs)': ['*']
            }
        });
        if (!picked?.[0])
            return;
        const response = await uploadFile(context, caseId, picked[0], `/cases/${caseId}/artifacts`);
        const c = await config(context);
        const parse = await axios_1.default.post(`${c.backend}/cases/${caseId}/artifacts/${response.data.id}/parse`, {}, { headers: c.headers });
        vscode.window.showInformationMessage(`Uploaded. Parse job: ${parse.data.id}`);
    }));
    context.subscriptions.push(vscode.commands.registerCommand('gwap.uploadWorkspace', async () => {
        const caseId = await caseIdPrompt();
        if (!caseId)
            return;
        const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
        if (!root)
            return vscode.window.showWarningMessage('Open a workspace first');
        await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: 'Packaging and uploading workspace', cancellable: false }, async () => {
            const zip = await zipWorkspace(root);
            try {
                const response = await uploadFile(context, caseId, vscode.Uri.file(zip), `/cases/${caseId}/repositories`);
                const c = await config(context);
                const job = await axios_1.default.post(`${c.backend}/repositories/${response.data.repository_id}/index`, {}, { headers: c.headers });
                vscode.window.showInformationMessage(`Repository uploaded. Index job: ${job.data.id}`);
            }
            finally {
                fs.rmSync(zip, { force: true });
            }
        });
    }));
    context.subscriptions.push(vscode.commands.registerCommand('gwap.askSelection', async () => {
        const caseId = await caseIdPrompt();
        if (!caseId)
            return;
        const editor = vscode.window.activeTextEditor;
        if (!editor)
            return;
        const selected = editor.document.getText(editor.selection) || editor.document.getText().slice(0, 6000);
        const question = await vscode.window.showInputBox({ prompt: 'Question about selected code', value: '结合当前故障，分析这段代码是否相关' });
        if (!question)
            return;
        const c = await config(context);
        const payload = { question: `${question}\n文件：${vscode.workspace.asRelativePath(editor.document.uri)}\n代码：\n${selected}` };
        const response = await axios_1.default.post(`${c.backend}/cases/${caseId}/chat`, payload, { headers: c.headers, timeout: 180000 });
        const doc = await vscode.workspace.openTextDocument({ language: 'markdown', content: `# GW/AP Debug Answer\n\n${response.data.answer}\n\n## Citations\n${response.data.citations.map((x) => `- ${x.evidence_id}: ${x.title || x.source_file || x.source_type}`).join('\n')}` });
        await vscode.window.showTextDocument(doc, { preview: false });
    }));
    context.subscriptions.push(vscode.commands.registerCommand('gwap.openCase', async () => {
        const caseId = await caseIdPrompt();
        if (!caseId)
            return;
        await vscode.env.openExternal(vscode.Uri.parse(`${workspaceConfig().web}/cases/${caseId}`));
    }));
}
function deactivate() { }
//# sourceMappingURL=extension.js.map