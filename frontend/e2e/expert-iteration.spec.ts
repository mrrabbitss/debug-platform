import { expect, test, type Page } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
const stamp = '2026-09-09T10:00:00Z'
const output = resolve(import.meta.dirname, '../node_modules/.cache/expert-iteration-20260909')
const roles = { diagnosis: '综合诊断', log_analysis: '日志分析', fault_tree: '故障树', report_template: '报告格式', prior_knowledge: '先验知识' }
const model = (id: string, visibility: string, can_manage: boolean, active = false) => ({ id, name: id === 'SHARED' ? '团队诊断模型' : '我的诊断模型', task_type: 'chat', mode: 'api', provider: 'openai_compatible', model_name: 'synthetic-chat', base_url: 'http://synthetic-model.invalid/v1', api_key_configured: true, api_key_hint: 'NEVER-SHOW-SHARED-KEY', proxy_url_configured: false, config: { timeout_seconds: 300 }, enabled: true, is_active: active, active, visibility, owner_id: visibility==='PRIVATE'?'USER-1':'MANAGER', can_manage, created_at: stamp, updated_at: stamp })
const contribution = (id: string, status: string, kind = 'KNOWLEDGE', owner = 'USER-1') => ({ id, owner_id: owner, operation: 'CREATE', content_kind: kind, status, version: 3, content_hash: 'a'.repeat(64), candidate: { title: id==='REVIEW'?'待审案例结论':'我的 Wiki 草稿', content: '# 合成知识\n核对日志中的支持证据和反证。', source_type: 'document', metadata: { problem_categories: ['network'] } }, original: { title: '原稿', content: '# 投稿原稿\n仍需要核对恢复情况。' }, diff: '-仍需要核对恢复情况。\n+核对日志中的支持证据和反证。', messages: [], revisions: [], created_at: stamp, updated_at: stamp })
const assistantSession = () => ({ id: 'ASSIST-1', title: '已审批的 Skill 整理', version: 9, status: 'PUBLISH_FAILED', created_at: stamp, model_egress_approved: true, mode: 'edit', review_digest: 'a'.repeat(64), messages: [], files: [{path:'synthetic/SKILL.md'}], coverage: {'synthetic/SKILL.md':{read:1,total:1,complete:true}}, plan: [{operation_id:'OP-1',action:'create',title:'合成 Skill',categories:['network'],role:'diagnosis',source_paths:['synthetic/SKILL.md'],reason:'合成测试',before:'',after:'# 合成 Skill',diff:'+# 合成 Skill'}], job:{id:'ASSIST-JOB',status:'FAILED',progress:40,message:'合成发布中断'}, error:'合成发布中断，审批保留' })

async function mock(page: Page, role = 'ENGINEER') {
  const state = {
    role, preference: 'PRIVATE' as string | null, models: [model('SHARED','SHARED',['ADMIN','EXPERT'].includes(role),true),model('PRIVATE','PRIVATE',role!=='VIEWER')],
    categories: [{id:'network',name:'组网问题',version:1,skill_status:'DEDICATED'},{id:'connection',name:'连接问题',version:1,skill_status:'NONE'},{id:'general',name:'通用知识',version:1,skill_status:'NONE'},{id:'unknown',name:'未知',version:1,skill_status:'NONE'}],
    documents: [
      {id:'WIKI',title:'共享知识 Wiki',version:2,lock_version:2,status:'ACTIVE',content_kind:'KNOWLEDGE',content:'# 共享经验\n先核对恢复证据。',categories:['network'],role:'prior_knowledge',source_paths:['wiki.md']},
      {id:'SKILL',title:'组网总领 Skill',version:4,lock_version:4,status:'ACTIVE',content_kind:'SKILL',content:'# 必需方法\n完整扫描日志。',categories:['network'],role:'diagnosis',source_paths:['synthetic/SKILL.md']}
    ],
    contributions: [contribution('OWN','DRAFT'),contribution('REVIEW','SUBMITTED','KNOWLEDGE','AUTHOR-2')] as any[],
    users: [{id:'USER-1',username:'owner',display_name:'当前管理员',role:'ADMIN',active:true,created_at:stamp},{id:'OTHER',username:'colleague',display_name:'同事',role:'ENGINEER',active:true,created_at:stamp}],
    calls: [] as {path:string;method:string;data:any;url:URL;raw:string}[], unexpected: [] as string[], errors: [] as string[],
    conflict: false, assistant: assistantSession() as any, curation: null as any, library: [] as any[],
    diagnosticWarning: '', selectionError: '', bridgeFailOnce: false, atomicLibrary: false, resetState: null as any, maintenanceDocuments: [] as any[],
    caseInfo: {id:'CASE',title:'合成连接案例',description:'检查日志证据',device_type:'AP',status:'ANALYZED',created_at:stamp,owner_id:'USER-1',problem_category:'connection',chat_profile_id:'OLD-CASE-MODEL',model_egress_approved:false}
  }
  page.on('pageerror', error=>state.errors.push(error.message))
  await page.route('**/api/v1/**', async route=> {
    const request=route.request(), url=new URL(request.url()), path=url.pathname.replace('/api/v1',''), method=request.method(), raw=request.postDataBuffer()?.toString('utf8') || ''
    let data:any=null; try{data=JSON.parse(raw)}catch{}
    state.calls.push({path,method,data,url,raw})
    const ok=(json:any)=>route.fulfill({json}), fail=(status:number,detail:string)=>route.fulfill({status,json:{detail}})
    const principal={id:'USER-1',username:'synthetic-user',display_name:'合成测试用户',role:state.role,type:'user_token'}
    if(path==='/system/auth-info') return ok({mode:'rbac',simple_engineer_login:false,token_header:'X-API-Key'})
    if(path==='/system/me') return ok(principal)
    if(path==='/workbench/bootstrap') return ok({principal,categories:state.categories,knowledge_roles:roles,models:state.models.filter(item=>item.enabled),preferences:{chat_profile_id:state.preference},model_selection:{profile_id:state.selectionError ? null : state.preference || 'SHARED',error:state.selectionError || null}})
    if(path==='/workbench/preferences') {state.preference=data.chat_profile_id;return ok(data)}
    if(path==='/system/models'&&method==='GET') return ok(state.models)
    if(path==='/system/models'&&method==='POST') { const item={...model('NEW',data.visibility,true),...data,api_key:undefined,api_key_configured:!!data.api_key};state.models.push(item);return ok(item) }
    if(/^\/system\/models\/[^/]+$/.test(path)) { const item=state.models.find(item=>item.id===path.split('/')[3]); if(method==='PATCH'){ Object.assign(item!,{...data,api_key:undefined});return ok(item) };if(method==='DELETE'){state.models=state.models.filter(model=>model!==item);return ok({deleted:true})} }
    if(path.endsWith('/activate')) {state.models.forEach(item=>{item.is_active=item.active=path.includes(`/${item.id}/`)});return ok({requires_reindex:false})}
    if(path.endsWith('/test')&&path.startsWith('/system/models')) return ok({ok:true,message:'合成连接成功'})
    if(path==='/system/users'&&method==='GET') return ok(state.users)
    if(path==='/system/users/OTHER'&&method==='PATCH') {Object.assign(state.users[1],data);return ok(state.users[1])}
    if(path==='/system/audit') return ok([{id:'AUDIT',created_at:stamp,actor_id:'AUTHOR',action:'knowledge.review',outcome:'SUCCESS',resource_type:'knowledge',resource_id:'DOC',details:{content_recorded:false}}])
    if(path==='/system/status') return ok({status:'ready',database:{dialect:'sqlite'},storage:{},jobs:{counts:{}},entities:{}})
    if(path==='/evaluation/datasets'||path==='/memory-governance/candidates') return ok([])
    if(path==='/knowledge/graph/status') return ok({status:'NOT_BUILT',entities:0,relations:0,metadata:{}})
    if(path==='/cases'&&method==='GET') return ok([])
    if(path==='/cases/CASE') { if(method==='PATCH')Object.assign(state.caseInfo,data);return ok(state.caseInfo) }
    if(path==='/cases/CASE/analyses') return ok([{id:'ANALYSIS',case_id:'CASE',status:'COMPLETED',created_at:stamp,evidence_json:'[]',result_json:JSON.stringify({summary:'合成结论',diagnostic_planning:{method_coverage:{skill_status:{warning:state.diagnosticWarning}}},hypotheses:[],recommended_actions:[],limitations:[state.diagnosticWarning]})}])
    if(path==='/cases/CASE/access') return ok({case_id:'CASE',permission:'OWNER',role:state.role})
    if(path==='/cases/CASE/events/stats') return ok({total:0,filtered_total:0,level_counts:{},module_counts:{}})
    if(['/cases/CASE/artifacts','/cases/CASE/repositories','/cases/CASE/members','/system/user-directory'].includes(path)) return ok([])
    if(path==='/workbench/knowledge') return ok(state.documents)
    if(path==='/workbench/library') {if(method==='POST'){const item={...data,id:'LIBRARY',version:1,owner_id:'USER-1',status:'PENDING'};state.library.push(item);if(state.atomicLibrary){state.contributions.push({...contribution('LIB-CONTRIB','SUBMITTED'),source_library_id:'LIBRARY'});return ok({...item,contribution_id:'LIB-CONTRIB'})}return ok(item)}return ok(state.library)}
    if(path==='/knowledge') return ok(state.maintenanceDocuments)
    if(path==='/knowledge/LEGACY'&&method==='DELETE') {const item={...contribution('DELETE-PENDING','PUBLISHING'),operation:'DELETE',publication_job_id:'DELETE-JOB'};state.contributions.push(item);return ok({publication_pending:true,contribution:item,job:{id:'DELETE-JOB',status:'QUEUED'}})}
    if(path==='/workbench/knowledge-reset/preview') return ok({operation_id:data.operation_id,target:{data_root:data.data_root,database_path:`${data.data_root}/synthetic.sqlite3`},source_sha256:'d'.repeat(64),preview_hash:'e'.repeat(64),counts:{knowledge_and_skills:2,currently_active:2,skills:1,drafts:0,preserved_publications:1},manifest:[{path:'synthetic/SKILL.md',role:'diagnosis',bytes:100,source_sha256:'f'.repeat(64),sha256:'f'.repeat(64),references:[],adaptation_diff:'-外部工具\n+平台证据接口'}],preserved:['cases','reports']})
    if(path==='/workbench/knowledge-reset/confirm') {state.resetState={operation:{status:'APPROVED',operation_id:data.operation_id,backup:{path:'synthetic/before.sqlite3',sha256:'b'.repeat(64)}},job:{id:'RESET-JOB',status:'QUEUED',message:'审批已保存'}};return ok(state.resetState)}
    if(path.startsWith('/workbench/knowledge-reset/')) return ok(state.resetState)
    if(path==='/knowledge/categories') return ok([{id:'HISTORY',code:'history.cases',name:'案例分类',active:true,document_count:0,description:'',sort_order:0}])
    if(path==='/knowledge/SKILL') return ok({id:'SKILL',title:'组网总领 Skill',content:'# 必需方法\n完整扫描日志。',source_type:'analysis_skill',version:4,lock_version:4,active:true,review_status:'ACTIVE',metadata:{content_kind:'SKILL',knowledge_role:'diagnosis',problem_categories:['network'],source_paths:['synthetic/SKILL.md'],bundle_id:'synthetic-bundle'},can_publish:true})
    if(path==='/workbench/categories'&&method==='POST') {const item={id:'NEW-CATEGORY',name:data.name,version:1,skill_status:'NONE'};state.categories.push(item);return ok(item)}
    if(path.startsWith('/workbench/categories/')&&method==='PATCH') {const item=state.categories.find(x=>x.id===path.split('/').at(-1))!;if(item.version!==data.version)return fail(409,'类别已变化');item.name=data.name;item.version++;return ok(item)}
    if(path.startsWith('/workbench/categories/')&&method==='DELETE') {state.categories=state.categories.filter(x=>x.id!==path.split('/').at(-1));return ok({deactivated:true})}
    if(path==='/knowledge-contributions'&&method==='GET') return ok(state.contributions.filter(item=>(role==='ADMIN'||role==='EXPERT')&&url.searchParams.get('mine')!=='true'||item.owner_id==='USER-1').filter(item=>!url.searchParams.get('status')||item.status===url.searchParams.get('status')))
    if(path==='/knowledge-contributions'&&method==='POST') {
      const source=state.documents.find(item=>item.id===data.target_document_id), id=`NEW-${state.contributions.length}`
      const item={...contribution(id,'DRAFT',source?.content_kind || data.content_kind || 'KNOWLEDGE'),operation:data.operation, target_document_id:data.target_document_id,candidate:{title:data.title||source?.title,content:data.content||source?.content,metadata:data.metadata||{problem_categories:source?.categories}},original:{content:source?.content||''}}
      state.contributions.unshift(item);return ok(item)
    }
    if(path==='/knowledge-contributions/from-library/LIBRARY') {if(state.bridgeFailOnce){state.bridgeFailOnce=false;return fail(503,'合成暂时故障')};const item={...contribution('LIB-CONTRIB','SUBMITTED'),source_library_id:'LIBRARY'};state.contributions.push(item);return ok(item)}
    if(path.startsWith('/knowledge-contributions/')) {
      const id=path.split('/')[2], item=state.contributions.find(item=>item.id===id), action=path.split('/')[3]
      if(!item)return fail(404,'Not found')
      if(method==='GET') return ok(item)
      if(method==='DELETE') {if(Number(url.searchParams.get('expected_version'))!==item.version)return fail(409,'Version required');state.contributions=state.contributions.filter(x=>x!==item);return ok({deleted:id})}
      if(data.expected_version!==item.version)return fail(409,'版本已变化，请刷新')
      if(method==='PATCH'){Object.assign(item.candidate,{title:data.title??item.candidate.title,content:data.content??item.candidate.content});item.version++;item.content_hash='b'.repeat(64);return ok(item)}
      if(action==='submit'){item.status='SUBMITTED';item.version++;return ok(item)}
      if(action==='review-chat'){item.candidate.content+=`\n已根据第 ${item.version} 轮要求补充反证。`;item.version++;item.content_hash=String(item.version%10).repeat(64);item.diff='-原始文本\n+多轮修订后文本';item.messages.push({role:'user',content:data.instruction},{role:'assistant',content:'已经修订，请复核。'});return ok(item)}
      if(action==='review') {if(state.conflict){state.conflict=false;item.version++;item.content_hash='c'.repeat(64);return fail(409,'内容已变化，请刷新后重新核对')};if(data.expected_content_hash!==item.content_hash)return fail(409,'Hash mismatch');item.status=data.action==='APPROVE'?'PUBLISHED':data.action==='RETURN'?'RETURNED':'REJECTED';item.version++;return ok(item)}
    }
    if(path==='/workbench/assistant')return ok([state.assistant])
    if(path==='/workbench/assistant/ASSIST-1')return ok(state.assistant)
    if(path==='/workbench/assistant/ASSIST-1/retry'){state.assistant.status='PUBLISHED';state.assistant.version++;state.assistant.error='';state.assistant.job={status:'COMPLETED',progress:100};return ok(state.assistant)}
    if(path==='/knowledge-curations'&&method==='GET')return ok(state.curation?[state.curation]:[])
    if(path==='/knowledge-curations'&&method==='POST') {
      state.curation={id:'CURATION',title_hint:'合成案例',draft_title:'合成提炼结果',draft_markdown:'# 案例\n日志证据与验证。'.repeat(10),draft_version:1,status:'REVIEWING',validation:{confirmable:true,errors:[],warnings:[]},messages:[],sources:[],revisions:[],source_count:1,created_at:stamp,updated_at:stamp,manifest:{}}
      return ok({session:state.curation,job:{id:'CURATION-JOB',status:'COMPLETED'}})
    }
    if(path==='/knowledge-curations/CURATION')return ok(state.curation)
    if(path==='/knowledge-curations/CURATION/confirm'){state.curation.status='CONFIRMED';const item={...contribution('EXTRACTED','DRAFT'),source_curation_id:'CURATION'};state.contributions.unshift(item);return ok({session:state.curation,contribution:item})}
    state.unexpected.push(`${method} ${path}`);return fail(501,`Unexpected ${method} ${path}`)
  })
  return state
}
async function clean(state: Awaited<ReturnType<typeof mock>>) {expect(state.errors).toEqual([]);expect(state.unexpected).toEqual([])}
async function choose(page:Page,label:string,option:string){await page.locator('.el-select').filter({has:page.getByRole('combobox',{name:label,exact:true})}).click();await page.getByRole('option',{name:option,exact:true}).click()}

test('ordinary three-section navigation and readonly Skills reject manager deep links',async({page})=>{
  const state=await mock(page);await page.goto('/knowledge');await expect(page.getByRole('tab',{name:'知识 Wiki',exact:true})).toBeVisible();await expect(page.getByRole('menuitem')).toHaveCount(3)
  await expect(page.getByRole('button',{name:'共享知识 Wiki'})).toBeVisible();await page.getByRole('tab',{name:'诊断 Skill · 只读'}).click();await page.getByRole('button',{name:'组网总领 Skill'}).click();await expect(page.getByRole('button',{name:'提出修改建议'})).toBeVisible();await expect(page.getByRole('button',{name:'新增 Skill 文件'})).toHaveCount(0)
  await page.goto('/knowledge-management');await expect(page).toHaveURL(/\/settings\?notice=role-required/);expect(state.calls.filter(x=>x.path==='/knowledge-contributions')).toHaveLength(0);await clean(state)
})

test('expert sees direct management and audit without user or GGUF requests',async({page})=>{
  const state=await mock(page,'EXPERT');await page.goto('/settings?tab=security');await expect(page.getByRole('menuitem',{name:'知识库管理'})).toBeVisible();await expect(page.getByText('knowledge.review',{exact:true})).toBeVisible();await expect(page.getByText('用户与角色',{exact:true})).toHaveCount(0);await expect(page.getByRole('tab',{name:'全局检索与 GGUF'})).toHaveCount(0)
  expect(state.calls.some(x=>['/system/users','/system/model-downloads','/system/retrieval'].includes(x.path))).toBe(false);await page.goto('/admin/models');await expect(page).toHaveURL(/admin-required/);await clean(state)
})

test('administrator can grant expert from security role editor',async({page})=>{
  const state=await mock(page,'ADMIN');await page.goto('/settings?tab=security');const row=page.getByRole('row').filter({hasText:'colleague'});await row.locator('.el-select').click();await page.getByRole('option',{name:'专家',exact:true}).click();await expect.poll(()=>state.users[1].role).toBe('EXPERT');expect(state.calls.find(x=>x.path==='/system/users/OTHER')?.data).toEqual({role:'EXPERT'});await clean(state)
})

test('ordinary custom HTTP API stays private and grouped preference is personal',async({page})=>{
  const state=await mock(page);await page.goto('/settings');await page.getByRole('button',{name:'添加诊断 API',exact:true}).click();await page.getByRole('textbox',{name:'配置名称',exact:true}).fill('个人内网模型');await page.getByRole('textbox',{name:'模型名称',exact:true}).fill('synthetic');await page.getByRole('textbox',{name:'Base URL',exact:true}).fill('http://10.22.33.44:9999/custom/v1');await page.getByRole('textbox',{name:'模型 API Key'}).fill('SYNTHETIC-INPUT-KEY');await expect(page.getByText('模型使用范围',{exact:true})).toHaveCount(0);await page.getByRole('button',{name:'保存诊断 API'}).click()
  await expect.poll(()=>state.models.length).toBe(3);const call=state.calls.find(x=>x.path==='/system/models'&&x.method==='POST')!;expect(call.data.visibility).toBe('PRIVATE');expect(call.data.base_url).toBe('http://10.22.33.44:9999/custom/v1');await choose(page,'个人诊断模型','个人内网模型');await page.getByRole('button',{name:'保存偏好'}).click();await expect.poll(()=>state.preference).toBe('NEW');expect(state.calls.some(x=>x.path.endsWith('/activate'))).toBe(false);await expect(page.locator('body')).not.toContainText('NEVER-SHOW-SHARED-KEY');await clean(state)
})

test('expert defaults new models to shared and can pick private without global activation',async({page})=>{
  const state=await mock(page,'EXPERT');await page.goto('/settings');await page.getByRole('button',{name:'添加诊断 API',exact:true}).click();await expect(page.getByRole('radio',{name:'全员共享',exact:true})).toBeChecked();await page.getByRole('radio',{name:'仅自己使用',exact:true}).locator('..').click();await page.getByRole('textbox',{name:'配置名称',exact:true}).fill('专家私有模型');await page.getByRole('textbox',{name:'模型名称',exact:true}).fill('synthetic');await page.getByRole('textbox',{name:'Base URL',exact:true}).fill('https://private-model.invalid/v1');await page.getByRole('button',{name:'保存诊断 API'}).click();await expect.poll(()=>state.models.length).toBe(3);expect(state.models[2].visibility).toBe('PRIVATE');await expect(page.getByRole('row').filter({hasText:'专家私有模型'}).getByRole('button',{name:'设为共享默认'})).toHaveCount(0);await clean(state)
})

test('Wiki Markdown filename grants no Skill power; owner edits and deletes a versioned draft',async({page})=>{
  const state=await mock(page);await page.goto('/knowledge');await page.getByRole('button',{name:'上传 Wiki Markdown'}).click();await page.getByLabel('上传 Wiki Markdown',{exact:true}).setInputFiles({name:'SKILL.md',mimeType:'text/markdown',buffer:Buffer.from('# 普通知识\n合成文本，不赋予 Skill 管理权限。')});await page.getByRole('button',{name:'保存我的草稿'}).click();await expect(page.getByRole('textbox',{name:'提交 Markdown'})).toBeVisible();const created=state.contributions[0];expect(created.content_kind).toBe('KNOWLEDGE');await page.getByRole('textbox',{name:'提交 Markdown'}).fill('# 修改\n增加验证步骤。');await page.getByRole('button',{name:'保存修改',exact:true}).click();await expect.poll(()=>created.version).toBe(4);await page.getByRole('button',{name:'删除我的草稿'}).click();await page.getByRole('button',{name:'确定',exact:true}).click();await expect.poll(()=>state.contributions.some(x=>x.id===created.id)).toBe(false);expect(state.calls.find(x=>x.method==='DELETE')?.url.searchParams.get('expected_version')).toBe('4');await clean(state)
})

test('ordinary shared Wiki deletion and Skill amendments become proposals',async({page})=>{
  const state=await mock(page);await page.goto('/knowledge');await page.getByRole('button',{name:'共享知识 Wiki'}).click();await page.getByRole('button',{name:'申请删除',exact:true}).click();await page.getByRole('button',{name:'保存我的草稿'}).click();await page.getByRole('button',{name:'提交审核',exact:true}).click();await expect.poll(()=>state.contributions[0].status).toBe('SUBMITTED');expect(state.contributions[0].operation).toBe('DELETE');expect(state.documents[0].status).toBe('ACTIVE')
  await page.goto('/knowledge?tab=skills');await page.getByRole('button',{name:'组网总领 Skill'}).click();await page.getByRole('button',{name:'提出修改建议'}).click();await page.getByRole('textbox',{name:'新知识 Markdown'}).fill('# 建议\n补充反证。');await page.getByRole('button',{name:'保存我的草稿'}).click();await expect.poll(()=>state.contributions[0].content_kind).toBe('SKILL');expect(state.calls.some(x=>x.method!=='GET'&&/^\/knowledge\//.test(x.path))).toBe(false);await clean(state)
})

test('reviewer compares original and diff, performs multiple AI revisions, approves exact final hash',async({page})=>{
  const state=await mock(page,'EXPERT');await page.goto('/knowledge-management?tab=review');await page.getByRole('button',{name:'核对并审核',exact:true}).click();await page.getByRole('tab',{name:'提交原稿'}).click();await expect(page.getByText('# 投稿原稿',{exact:false})).toBeVisible();await page.getByRole('tab',{name:'修改差异'}).click();await expect(page.getByText('-仍需要核对恢复情况。',{exact:false})).toBeVisible()
  for(const text of ['补充反证','再核对日志时间']){await page.getByRole('textbox',{name:'AI 修正要求'}).fill(text);await expect(page.getByRole('button',{name:'批准并发布',exact:true})).toBeDisabled();await page.getByRole('button',{name:'发送修正要求'}).click();await expect(page.getByRole('textbox',{name:'AI 修正要求'})).toHaveValue('')}
  const item=state.contributions.find(x=>x.id==='REVIEW');await page.getByRole('checkbox',{name:/已核对 v5/}).locator('..').click();await page.getByRole('button',{name:'批准并发布',exact:true}).click();await expect.poll(()=>item.status).toBe('PUBLISHED');const approval=state.calls.find(x=>x.path==='/knowledge-contributions/REVIEW/review')!;expect(approval.data.expected_version).toBe(5);expect(approval.data.expected_content_hash).toBe('5'.repeat(64));await clean(state)
})

test('review conflict never replays approval and requires another explicit review',async({page})=>{
  const state=await mock(page,'EXPERT');state.conflict=true;await page.goto('/knowledge-management?tab=review');await page.getByRole('button',{name:'核对并审核',exact:true}).click();await page.getByRole('checkbox',{name:/已核对 v3/}).locator('..').click();await page.getByRole('button',{name:'批准并发布',exact:true}).click();await expect(page.getByText('内容已变化，请刷新后重新核对',{exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'批准并发布',exact:true})).toBeDisabled();await page.getByRole('button',{name:'刷新并重新核对'}).click();await expect(page.getByRole('checkbox',{name:/已核对 v4/})).not.toBeChecked();expect(state.calls.filter(x=>x.path==='/knowledge-contributions/REVIEW/review')).toHaveLength(1);await clean(state)
})

test('new category appears in case creation with evidence-only Skill warning',async({page})=>{
  const state=await mock(page,'EXPERT');await page.goto('/knowledge-management');await page.getByRole('button',{name:'新增问题类别',exact:true}).click();await page.locator('.el-message-box__input input').fill('漫游问题');await page.getByRole('button',{name:'确定',exact:true}).click();await expect.poll(()=>state.categories.length).toBe(5);await page.getByRole('menuitem',{name:'故障定位'}).click();await page.getByRole('button',{name:'创建待定位案例'}).click();await choose(page,'问题类别','漫游问题');await expect(page.getByText(/未使用任何 Skill：此类别与通用知识/)).toBeVisible();await expect(page.getByRole('button',{name:'创建并上传日志'})).toBeEnabled();await clean(state)
})

test('expert direct Skill edit preserves bundle metadata and publishes a reviewed version',async({page})=>{
  const state=await mock(page,'EXPERT');await page.goto('/knowledge-management');await page.getByRole('row').filter({hasText:'组网总领 Skill'}).getByRole('button',{name:'编辑',exact:true}).click();await page.getByRole('textbox',{name:'Skill Markdown',exact:true}).fill('# 方法更新\n先证据，后结论。');await page.getByRole('button',{name:'保存变更并核对'}).click();await expect(page.getByRole('checkbox',{name:/已核对 v3/}).locator('..')).toBeVisible();const item=state.contributions[0];expect(item.candidate.metadata.bundle_id).toBe('synthetic-bundle');expect(item.content_kind).toBe('SKILL');await page.getByRole('checkbox',{name:/已核对 v3/}).locator('..').click();await page.getByRole('button',{name:'批准并发布此版本'}).click();await expect.poll(()=>item.status).toBe('PUBLISHED');const approval=state.calls.find(x=>x.path.endsWith(`/${item.id}/review`));expect(approval?.data.expected_version).toBe(4);await clean(state)
})

test('assistant publication retry preserves approval without calling confirm',async({page})=>{
  const state=await mock(page,'EXPERT');await page.goto('/knowledge-management?tab=assistant');await page.getByRole('button',{name:/已审批的 Skill 整理/}).click();await expect(page.getByText('已审批，发布失败',{exact:true})).toBeVisible();await page.getByRole('button',{name:'重试已审批的发布'}).click();await expect.poll(()=>state.assistant.status).toBe('PUBLISHED');expect(state.calls.filter(x=>x.path.endsWith('/confirm'))).toHaveLength(0);expect(state.calls.find(x=>x.path.endsWith('/retry'))?.data).toEqual({version:9});await clean(state)
})

test('ordinary AI case extraction defaults consent and unified model then submits its private result',async({page})=>{
  const state=await mock(page);await page.goto('/knowledge?tab=submissions&extract=1');await page.getByTestId('curation-open-create').click();await expect(page.getByTestId('curation-egress-consent').getByRole('checkbox')).toBeChecked();const folder=resolve(output,'synthetic-case');mkdirSync(folder,{recursive:true});writeFileSync(resolve(folder,'case.md'),'# Synthetic\nOnly generated test evidence.');await page.locator('input[webkitdirectory]').setInputFiles(folder);await page.getByTestId('curation-submit').click();await expect(page.getByTestId('curation-confirm')).toBeEnabled();const creation=state.calls.find(x=>x.path==='/knowledge-curations'&&x.method==='POST')!;expect(creation.raw).toContain('true');expect(creation.raw).not.toContain('name="model_profile_id"');await page.getByTestId('curation-confirm').click();await page.getByRole('button',{name:'确认并提交审核',exact:true}).click();await expect.poll(()=>state.contributions.find(x=>x.id==='EXTRACTED')?.status).toBe('SUBMITTED');await expect(page).toHaveURL(/contribution=EXTRACTED/);await clean(state)
})

test('expert narrow screen navigation and management stay within viewport',async({page})=>{
  const state=await mock(page,'EXPERT');await page.setViewportSize({width:390,height:844});await page.goto('/knowledge-management');await expect(page.getByRole('button',{name:'新增 Skill 文件'})).toBeVisible();await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1)).toBe(true);mkdirSync(output,{recursive:true});await page.screenshot({path:resolve(output,'expert-mobile.png'),fullPage:true});await clean(state)
})

for (const [mode,warning] of [
  ['GENERAL_ONLY','缺少专属 Skill：本次使用通用 Skill。'],
  ['EVIDENCE_ONLY','未使用任何 Skill：本次仅依据日志证据诊断。'],
  ['CROSS_CATEGORY','未知问题跨类别查找 Skill，类别建议需要核对。']
]) test(`final integration diagnosis warning is prominent for ${mode}`,async({page})=>{
  const state=await mock(page);state.diagnosticWarning=warning;await page.goto('/cases/CASE');await expect(page.getByText('我的诊断模型',{exact:true})).toBeVisible();await expect(page.getByRole('combobox',{name:'案例诊断模型'})).toHaveCount(0)
  await page.getByRole('textbox',{name:'案例问题现象'}).fill('补充后的问题现象');await page.getByRole('button',{name:'保存问题资料'}).click();await expect.poll(()=>state.caseInfo.description).toBe('补充后的问题现象');const update=state.calls.find(x=>x.path==='/cases/CASE'&&x.method==='PATCH')!;expect(update.data).toEqual({problem_category:'connection',description:'补充后的问题现象'});expect(state.caseInfo.chat_profile_id).toBe('OLD-CASE-MODEL');expect(state.caseInfo.model_egress_approved).toBe(false)
  await page.getByRole('tab',{name:'综合诊断',exact:true}).click();const alert=page.getByTestId('diagnosis-skill-warning');await expect(alert).toBeVisible();await expect(alert).toContainText(warning);expect(await alert.evaluate(node=>!!node.closest('details'))).toBe(false);await expect(page.locator('details').filter({has:page.getByText('诊断方法与故障树覆盖',{exact:true})})).not.toHaveAttribute('open');await clean(state)
})

test('final integration category rename and deactivate use fresh version bodies',async({page})=>{
  const state=await mock(page,'EXPERT');await page.goto('/knowledge-management');await page.getByRole('button',{name:'连接问题',exact:true}).click();await page.getByRole('button',{name:'修改名称',exact:true}).click();await page.locator('.el-message-box__input input').fill('终端连接问题');await page.getByRole('button',{name:'确定',exact:true}).click();await expect(page.getByRole('button',{name:'终端连接问题',exact:true})).toBeVisible();expect(state.calls.find(x=>x.path==='/workbench/categories/connection')?.data).toEqual({name:'终端连接问题',version:1})
  await page.getByRole('button',{name:'停用类别',exact:true}).click();await page.getByRole('button',{name:'确定',exact:true}).click();await expect(page.getByRole('button',{name:'终端连接问题',exact:true})).toHaveCount(0);expect(state.calls.find(x=>x.path==='/workbench/categories/connection'&&x.method==='DELETE')?.data).toEqual({version:2});await page.getByRole('button',{name:'通用知识',exact:true}).click();await expect(page.getByRole('button',{name:'停用类别',exact:true})).toBeDisabled();await clean(state)
})

test('final integration reset previews exact scope and resumes saved operation without reapproval',async({page})=>{
  const state=await mock(page,'EXPERT');await page.goto('/knowledge-management?tab=maintenance');await page.getByRole('button',{name:'高级维护：一次性知识重置与组网包导入'}).click();await page.getByRole('textbox',{name:'服务器数据根目录'}).fill('D:/synthetic/old');await page.getByRole('button',{name:'预览清理与导入清单'}).click();await expect(page.getByRole('button',{name:'确认备份并重置导入'})).toBeDisabled()
  await page.getByRole('textbox',{name:'服务器数据根目录'}).fill('D:/synthetic/final');await expect(page.getByRole('button',{name:'确认备份并重置导入'})).toHaveCount(0);await page.getByRole('button',{name:'预览清理与导入清单'}).click();await expect(page.getByText('+平台证据接口',{exact:false})).toBeVisible();await page.getByRole('checkbox',{name:'已核对全部清理范围、完整文件清单和适配差异'}).locator('..').click();await page.getByRole('button',{name:'确认备份并重置导入'}).click();await expect(page.getByText(/已审批，等待导入/)).toBeVisible()
  const sent=state.calls.find(x=>x.path==='/workbench/knowledge-reset/confirm')!.data;expect(sent).toEqual({operation_id:expect.any(String),data_root:'D:/synthetic/final',expected_source_sha256:'d'.repeat(64),expected_preview_hash:'e'.repeat(64),confirmed:true,model_egress_approved:true});expect(Object.keys(sent)).not.toContain('source_zip');state.resetState.operation.status='PUBLISHED';state.resetState.job.status='COMPLETED';state.resetState.job.message='合成导入已完成';await page.reload();await page.getByRole('button',{name:'高级维护：一次性知识重置与组网包导入'}).click();await expect(page.getByText(/已发布 · 合成导入已完成/)).toBeVisible();expect(state.calls.filter(x=>x.path==='/workbench/knowledge-reset/confirm')).toHaveLength(1);await clean(state)
})

test('final integration case submission retries only the existing record review bridge',async({page})=>{
  const state=await mock(page);state.bridgeFailOnce=true;await page.goto('/knowledge');await page.getByRole('button',{name:'提交已定位案例',exact:true}).click();await page.getByRole('textbox',{name:'提交案例标题'}).fill('合成定位结果');await page.getByRole('textbox',{name:'定位结果与验证'}).fill('合成根因与验证证据已经完成。');await page.getByRole('button',{name:'提交专家 / 管理员审核',exact:true}).click();await expect(page.getByText(/案例已保存，接入审批队列未完成/)).toBeVisible();await expect(page.getByRole('textbox',{name:'定位结果与验证'})).toBeDisabled();await page.getByRole('button',{name:'重试接入审批队列',exact:true}).click();await expect.poll(()=>state.contributions.some(x=>x.id==='LIB-CONTRIB'&&x.status==='SUBMITTED')).toBe(true);expect(state.calls.filter(x=>x.path==='/workbench/library'&&x.method==='POST')).toHaveLength(1);expect(state.calls.filter(x=>x.path==='/knowledge-contributions/from-library/LIBRARY')).toHaveLength(2);await clean(state)
})

test('atomic case submission is already in review without a second mutation',async({page})=>{
  const state=await mock(page);state.atomicLibrary=true;await page.goto('/knowledge');await page.getByRole('button',{name:'提交已定位案例',exact:true}).click();await page.getByRole('textbox',{name:'提交案例标题'}).fill('合成定位结果');await page.getByRole('textbox',{name:'定位结果与验证'}).fill('合成根因与验证证据已经完成。');await page.getByRole('button',{name:'提交专家 / 管理员审核',exact:true}).click();await expect(page.getByRole('dialog')).toBeHidden();expect(state.contributions.find(x=>x.id==='LIB-CONTRIB')?.status).toBe('SUBMITTED');expect(state.calls.filter(x=>x.path==='/workbench/library'&&x.method==='POST')).toHaveLength(1);expect(state.calls.filter(x=>x.path==='/knowledge-contributions/from-library/LIBRARY')).toHaveLength(0);await clean(state)
})

test('expert can open the existing candidate memory review panel',async({page})=>{
  const state=await mock(page,'EXPERT');await page.goto('/admin/quality-governance');await page.getByRole('tab',{name:'候选经验 / 记忆沉淀',exact:true}).click();await expect(page.getByRole('combobox',{name:'候选记忆状态'})).toBeVisible();await expect(page.getByRole('button',{name:'刷新候选',exact:true})).toBeVisible();await expect.poll(()=>state.calls.some(x=>x.path==='/memory-governance/candidates')).toBe(true);expect(state.calls.some(x=>x.path==='/system/users'||x.path.startsWith('/system/models'))).toBe(false);await clean(state)
})

test('final integration library opens approved shared text and preserves original report',async({page})=>{
  const state=await mock(page);state.library=[{id:'LIBRARY',title:'已审核案例',version:2,status:'CONFIRMED',owner_id:'OTHER',problem_category:'network',content:'提交时的原始结论',report_markdown:'# 原始诊断报告\n仍待核对',reviewed_conclusion:'# 审核后结论\n修订后的根因与验证'}];await page.goto('/knowledge?tab=library');await page.getByRole('button',{name:'阅读报告',exact:true}).click();await expect(page.getByTestId('library-record-content')).toContainText('修订后的根因与验证');await page.getByRole('radio',{name:'原始诊断报告',exact:true}).locator('..').click();await expect(page.getByTestId('library-record-content')).toContainText('仍待核对');await page.getByRole('radio',{name:'提交原稿',exact:true}).locator('..').click();await expect(page.getByTestId('library-record-content')).toHaveText('提交时的原始结论');await clean(state)
})

test('final integration unavailable personal model is explicit instead of a false fallback',async({page})=>{
  const state=await mock(page);state.selectionError='原个人模型已停用，请重新选择';await page.goto('/cases');await page.getByRole('button',{name:'创建待定位案例'}).click();await expect(page.getByText('当前模型选择不可用',{exact:true})).toBeVisible();await expect(page.getByText(state.selectionError,{exact:true})).toBeVisible();await expect(page.getByText('团队诊断模型',{exact:true})).toHaveCount(0);await clean(state)
})

test('final integration assistant review shows content kinds and blocks unsent corrections',async({page})=>{
  const state=await mock(page,'EXPERT');state.assistant.status='REVIEW';state.assistant.error='';state.assistant.job=null;state.assistant.plan[0].content_kind='SKILL';state.assistant.plan.push({...state.assistant.plan[0],operation_id:'OP-2',title:'案例知识',content_kind:'KNOWLEDGE'});await page.goto('/knowledge-management?tab=assistant');await page.getByRole('button',{name:/已审批的 Skill 整理/}).click();await expect(page.getByText('诊断 Skill',{exact:true})).toBeVisible();await expect(page.getByText('普通知识',{exact:true})).toBeVisible();await page.getByRole('row').filter({hasText:'案例知识'}).getByRole('button',{name:'查看差异'}).click();await expect(page.getByText('归位：普通知识 · 组网问题 · 综合诊断',{exact:true})).toBeVisible();await page.getByRole('button',{name:'返回变更清单'}).click();await page.getByRole('textbox',{name:'知识助手要求'}).fill('将第二项改为诊断 Skill');await expect(page.getByRole('button',{name:'确认并发布 2 项变更'})).toBeDisabled();expect(state.calls.filter(x=>x.path.endsWith('/confirm'))).toHaveLength(0);await clean(state)
})

test('legacy maintenance deletion opens durable publication progress instead of claiming completion',async({page})=>{
  const state=await mock(page,'EXPERT');state.maintenanceDocuments=[{id:'LEGACY',title:'历史管理文档',content:'# 合成知识',source_type:'document',version:2,lock_version:2,active:true,review_status:'ACTIVE',can_publish:true,chunk_count:1,metadata:{}}];await page.goto('/knowledge-management?tab=maintenance');await page.getByRole('row').filter({hasText:'历史管理文档'}).getByRole('button',{name:'删除',exact:true}).click();await page.getByRole('button',{name:'确定',exact:true}).click();await expect(page).toHaveURL(/tab=review&contribution=DELETE-PENDING/);await expect(page.getByText('审批已持久保存，索引构建成功后生效。可以关闭页面，服务重启后会继续处理。',{exact:true})).toBeVisible();await expect(page.getByText('知识已删除',{exact:true})).toHaveCount(0);expect(state.maintenanceDocuments).toHaveLength(1);await clean(state)
})
