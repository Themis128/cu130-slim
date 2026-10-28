import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest'
import axios from 'axios'

const localStorageMock = {
  getItem: vi.fn(),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn(),
}

Object.defineProperty(window, 'localStorage', {
  value: localStorageMock,
  writable: true,
})

const originalLocation = window.location
delete window.location
window.location = { href: '' } as Location

vi.mock('axios')
const mockedAxios = vi.mocked(axios)

type Verb = 'get' | 'post' | 'patch' | 'put' | 'delete'

// [label, invoke(apiModule) => promise, verb, expected first-arg URL]
type Case = [string, (m: any) => unknown, Verb, string]

const FILE = new File(['x'], 'f.png', { type: 'image/png' })
const ID = 'a1'
const ACC = 'acc1'

const CASES: Case[] = [
  // --- teams ---
  ['teams.list', (m) => m.teamsApi.list(), 'get', '/teams'],
  ['teams.create', (m) => m.teamsApi.create('Team'), 'post', '/teams'],
  ['teams.get', (m) => m.teamsApi.get(ID), 'get', `/teams/${ID}`],
  ['teams.update', (m) => m.teamsApi.update(ID, 'N'), 'patch', `/teams/${ID}`],
  ['teams.delete', (m) => m.teamsApi.delete(ID), 'delete', `/teams/${ID}`],
  ['teams.invite', (m) => m.teamsApi.invite(ID, 'e@x.com'), 'post', `/teams/${ID}/invite`],
  ['teams.acceptInvite', (m) => m.teamsApi.acceptInvite('tok'), 'post', '/teams/accept-invite'],
  ['teams.addMember', (m) => m.teamsApi.addMember(ID, 'u1'), 'post', `/teams/${ID}/members/u1`],
  ['teams.changeRole', (m) => m.teamsApi.changeRole(ID, 'u1', 'admin'), 'patch', `/teams/${ID}/members/u1`],
  ['teams.removeMember', (m) => m.teamsApi.removeMember(ID, 'u1'), 'delete', `/teams/${ID}/members/u1`],

  // --- billing ---
  ['billing.config', (m) => m.billingApi.config(), 'get', '/billing/config'],
  ['billing.plans', (m) => m.billingApi.plans(), 'get', '/billing/plans'],
  ['billing.subscription', (m) => m.billingApi.subscription(), 'get', '/billing/subscription'],
  ['billing.checkout', (m) => m.billingApi.checkout('pro'), 'post', '/billing/checkout'],
  ['billing.portal', (m) => m.billingApi.portal(), 'post', '/billing/portal'],
  ['billing.cancel', (m) => m.billingApi.cancel(), 'post', '/billing/cancel'],
  ['billing.sync', (m) => m.billingApi.sync(), 'post', '/billing/sync'],
  ['billing.discount', (m) => m.billingApi.discount(), 'get', '/billing/discount'],
  ['billing.setDiscount', (m) => m.billingApi.setDiscount('SAVE'), 'put', '/billing/discount'],
  ['billing.clearDiscount', (m) => m.billingApi.clearDiscount(), 'delete', '/billing/discount'],

  // --- content (approval + pillars + briefs) ---
  ['content.submitForReview', (m) => m.contentApi.submitForReview(ID), 'post', `/content/posts/${ID}/submit-review`],
  ['content.approvePost', (m) => m.contentApi.approvePost(ID, 'ok'), 'post', `/content/posts/${ID}/approve`],
  ['content.rejectPost', (m) => m.contentApi.rejectPost(ID), 'post', `/content/posts/${ID}/reject`],
  ['content.addComment', (m) => m.contentApi.addComment(ID, 'hi', 'approve'), 'post', `/content/posts/${ID}/comments`],
  ['content.getLinkPreview', (m) => m.contentApi.getLinkPreview('https://x'), 'get', '/content/link-preview'],
  ['content.listPillars', (m) => m.contentApi.listPillars(), 'get', '/content/pillars'],
  ['content.createPillar', (m) => m.contentApi.createPillar({ name: 'P' }), 'post', '/content/pillars'],
  ['content.updatePillar', (m) => m.contentApi.updatePillar(ID, { name: 'P' }), 'patch', `/content/pillars/${ID}`],
  ['content.deletePillar', (m) => m.contentApi.deletePillar(ID), 'delete', `/content/pillars/${ID}`],
  ['content.listBriefs', (m) => m.contentApi.listBriefs(), 'get', '/content/briefs'],
  ['content.createBrief', (m) => m.contentApi.createBrief({ title: 'T' }), 'post', '/content/briefs'],
  ['content.deleteBrief', (m) => m.contentApi.deleteBrief(ID), 'delete', `/content/briefs/${ID}`],

  // --- media ---
  ['media.bulkDelete', (m) => m.mediaApi.bulkDelete(['1', '2']), 'post', '/media/assets/bulk-delete'],
  ['media.getAsset', (m) => m.mediaApi.getAsset(ID), 'get', `/media/assets/${ID}`],
  ['media.generateVideo', (m) => m.mediaApi.generateVideo('prompt'), 'post', '/media/generate-video'],
  ['media.videoStatus', (m) => m.mediaApi.getVideoGenerationStatus('task1'), 'get', '/media/generate-video/task1'],

  // --- workflows ---
  ['wf.listTemplates', (m) => m.workflowApi.listTemplates('cat'), 'get', '/workflows/templates'],
  ['wf.getTemplate', (m) => m.workflowApi.getTemplate(ID), 'get', `/workflows/templates/${ID}`],
  ['wf.createTemplate', (m) => m.workflowApi.createTemplate({ name: 'T', prompt_template: 'p', n8n_workflow_json: {} }), 'post', '/workflows/templates'],
  ['wf.updateTemplate', (m) => m.workflowApi.updateTemplate(ID, { name: 'T' }), 'patch', `/workflows/templates/${ID}`],
  ['wf.deleteTemplate', (m) => m.workflowApi.deleteTemplate(ID), 'delete', `/workflows/templates/${ID}`],
  ['wf.generate', (m) => m.workflowApi.generateWorkflow({ prompt: 'p' }), 'post', '/workflows/generate'],
  ['wf.list', (m) => m.workflowApi.listWorkflows({ status: 'active' }), 'get', '/workflows'],
  ['wf.get', (m) => m.workflowApi.getWorkflow(ID), 'get', `/workflows/${ID}`],
  ['wf.deploy', (m) => m.workflowApi.deployWorkflow(ID), 'post', `/workflows/deploy/${ID}`],
  ['wf.delete', (m) => m.workflowApi.deleteWorkflow(ID), 'delete', `/workflows/${ID}`],
  ['wf.undeploy', (m) => m.workflowApi.undeployWorkflow(ID), 'post', `/workflows/${ID}/undeploy`],
  ['wf.executions', (m) => m.workflowApi.getExecutions(ID), 'get', `/workflows/${ID}/executions`],
  ['wf.listContentTemplates', (m) => m.workflowApi.listContentTemplates({ platform: 'x' }), 'get', '/workflows/content-templates'],
  ['wf.createContentTemplate', (m) => m.workflowApi.createContentTemplate({ name: 'T', system_prompt: 's', user_prompt_template: 'u' }), 'post', '/workflows/content-templates'],
  ['wf.updateContentTemplate', (m) => m.workflowApi.updateContentTemplate(ID, { name: 'T', system_prompt: 's', user_prompt_template: 'u' }), 'patch', `/workflows/content-templates/${ID}`],
  ['wf.deleteContentTemplate', (m) => m.workflowApi.deleteContentTemplate(ID), 'delete', `/workflows/content-templates/${ID}`],

  // --- accounts ---
  ['accounts.list', (m) => m.accountsApi.list(), 'get', '/accounts'],
  ['accounts.get', (m) => m.accountsApi.get(ID), 'get', `/accounts/${ID}`],
  ['accounts.connect', (m) => m.accountsApi.connect('x', 't1'), 'post', '/accounts/connect'],
  ['accounts.disconnect', (m) => m.accountsApi.disconnect(ID), 'delete', `/accounts/${ID}`],
  ['accounts.refresh', (m) => m.accountsApi.refresh(ID), 'post', `/accounts/${ID}/refresh`],
  ['accounts.validate', (m) => m.accountsApi.validate(ID), 'get', `/accounts/${ID}/validate`],
  ['accounts.test', (m) => m.accountsApi.test(ID), 'post', `/accounts/${ID}/test`],
  ['accounts.syncBusiness', (m) => m.accountsApi.syncBusinessAccounts(ID), 'post', `/accounts/${ID}/sync-business-accounts`],
  ['accounts.setBusiness', (m) => m.accountsApi.setBusinessAccount(ID, 'b1'), 'post', `/accounts/${ID}/set-business-account`],
  ['accounts.getPageProfile', (m) => m.accountsApi.getPageProfile(ID), 'get', `/accounts/${ID}/page-profile`],
  ['accounts.updatePageProfile', (m) => m.accountsApi.updatePageProfile(ID, { about: 'a' }), 'put', `/accounts/${ID}/page-profile`],
  ['accounts.uploadPic', (m) => m.accountsApi.uploadProfilePicture(ID, FILE), 'post', `/accounts/${ID}/page-profile/picture`],
  ['accounts.uploadCover', (m) => m.accountsApi.uploadCoverPhoto(ID, FILE), 'post', `/accounts/${ID}/page-profile/cover`],
  ['accounts.assignManage', (m) => m.accountsApi.assignManageTask(ID, 'b1', 'u1'), 'post', `/accounts/${ID}/page-profile/assign-manage-task`],

  // --- meta growth / profile ---
  ['metaGrowth.readiness', (m) => m.metaGrowthApi.readiness(), 'get', '/meta-growth/readiness'],
  ['metaGrowth.campaign', (m) => m.metaGrowthApi.createOrganicCampaign({ content_text: 't', media_ids: [] }), 'post', '/meta-growth/organic-campaign'],
  ['profile.get', (m) => m.profileApi.getProfile(ID), 'get', `/profile/${ID}`],
  ['profile.update', (m) => m.profileApi.updateProfile(ID, { about: 'a' }), 'put', `/profile/${ID}`],
  ['profile.uploadPic', (m) => m.profileApi.uploadProfilePicture(ID, FILE), 'post', `/profile/${ID}/picture`],
  ['profile.uploadCover', (m) => m.profileApi.uploadCoverPhoto(ID, FILE), 'post', `/profile/${ID}/cover`],
  ['profile.login', (m) => m.profileApi.login(ID, { username: 'u' }), 'post', `/profile/${ID}/login`],
  ['profile.threadsSettings', (m) => m.profileApi.getThreadsSettings(ID), 'get', `/profile/${ID}/threads/settings`],
  ['profile.updateThreadsSettings', (m) => m.profileApi.updateThreadsSettings(ID, {}), 'put', `/profile/${ID}/threads/settings`],

  // --- browser bridge ---
  ['browser.start', (m) => m.browserApi.startSession('x'), 'post', '/profile/browser/start'],
  ['browser.status', (m) => m.browserApi.getStatus(), 'get', '/profile/browser/status'],
  ['browser.cookies', (m) => m.browserApi.getCookies(), 'get', '/profile/browser/cookies'],
  ['browser.stop', (m) => m.browserApi.stopSession(), 'post', '/profile/browser/stop'],
  ['browser.extract', (m) => m.browserApi.extractCookies(), 'post', '/profile/browser/extract'],
  ['browser.novnc', (m) => m.browserApi.getNovncUrl(), 'get', '/profile/browser/novnc-url'],
  ['browser.platforms', (m) => m.browserApi.getPlatforms(), 'get', '/profile/browser/platforms'],
  ['browser.importIg', (m) => m.browserApi.importInstagramSession(), 'post', '/profile/browser/import-instagram-session'],
  ['browser.importTt', (m) => m.browserApi.importTiktokSession('s1', 'u1'), 'post', '/profile/tiktok/session'],
  ['browser.checkTt', (m) => m.browserApi.checkTiktokSession(), 'get', '/profile/tiktok/session'],
  ['browser.ttSettings', (m) => m.browserApi.getTiktokSettings(), 'get', '/profile/tiktok/settings'],

  // --- tiktok ---
  ['tiktok.health', (m) => m.tiktokApi.health(ACC), 'get', `/tiktok/accounts/${ACC}/health`],
  ['tiktok.creatorInfo', (m) => m.tiktokApi.creatorInfo(ACC), 'get', `/tiktok/accounts/${ACC}/creator-info`],
  ['tiktok.listVideos', (m) => m.tiktokApi.listVideos(ACC, { cursor: 1 }), 'get', `/tiktok/accounts/${ACC}/videos`],
  ['tiktok.queryVideos', (m) => m.tiktokApi.queryVideos(ACC, ['v1']), 'post', `/tiktok/accounts/${ACC}/videos/query`],
  ['tiktok.publishStatus', (m) => m.tiktokApi.publishStatus(ACC, 'p1'), 'post', `/tiktok/accounts/${ACC}/publish/status`],
  ['tiktok.publishCancel', (m) => m.tiktokApi.publishCancel(ACC, 'p1'), 'post', `/tiktok/accounts/${ACC}/publish/cancel`],
  ['tiktok.listUploads', (m) => m.tiktokApi.listUploads(ACC), 'get', `/tiktok/accounts/${ACC}/uploads`],

  // --- threads ---
  ['threads.getProfile', (m) => m.threadsApi.getProfile(ACC), 'get', '/threads/profile'],
  ['threads.updateProfile', (m) => m.threadsApi.updateProfile(ACC, { biography: 'b' }), 'put', '/threads/profile'],
  ['threads.insights', (m) => m.threadsApi.getInsights(ACC), 'get', '/threads/insights'],
  ['threads.followers', (m) => m.threadsApi.getFollowers(ACC), 'get', '/threads/followers'],
  ['threads.quota', (m) => m.threadsApi.getQuota(ACC), 'get', '/threads/quota'],
  ['threads.posts', (m) => m.threadsApi.listPosts(ACC), 'get', '/threads/posts'],
  ['threads.postInsights', (m) => m.threadsApi.getPostInsights('m1', ACC), 'get', '/threads/posts/m1/insights'],
  ['threads.reply', (m) => m.threadsApi.replyToPost('m1', ACC, 'hi'), 'post', '/threads/posts/m1/reply'],
  ['threads.delete', (m) => m.threadsApi.deletePost('m1', ACC), 'delete', '/threads/posts/m1'],

  // --- secrets ---
  ['secrets.list', (m) => m.secretsApi.list(), 'get', '/secrets'],
  ['secrets.get', (m) => m.secretsApi.get('K'), 'get', '/secrets/K'],
  ['secrets.set', (m) => m.secretsApi.set('K', 'v', 'd'), 'post', '/secrets/K'],

  // --- messenger ---
  ['msg.setup', (m) => m.messengerApi.setup(ACC), 'post', `/messenger/${ACC}/setup`],
  ['msg.getProfile', (m) => m.messengerApi.getProfile(ACC), 'get', `/messenger/${ACC}/profile`],
  ['msg.updateProfile', (m) => m.messengerApi.updateProfile(ACC, {}), 'put', `/messenger/${ACC}/profile`],
  ['msg.deleteFields', (m) => m.messengerApi.deleteProfileFields(ACC, ['greeting']), 'delete', `/messenger/${ACC}/profile`],
  ['msg.unsubscribe', (m) => m.messengerApi.unsubscribe(ACC), 'post', `/messenger/${ACC}/unsubscribe`],
  ['msg.send', (m) => m.messengerApi.sendMessage(ACC, { recipient_psid: 'p', text: 't' }), 'post', `/messenger/${ACC}/send`],
  ['msg.quickReplies', (m) => m.messengerApi.sendQuickReplies(ACC, { recipient_psid: 'p', text: 't', quick_replies: [] }), 'post', `/messenger/${ACC}/send-quick-replies`],
  ['msg.conversations', (m) => m.messengerApi.getConversations(ACC), 'get', `/messenger/${ACC}/conversations`],
  ['msg.convMessages', (m) => m.messengerApi.getConversationMessages(ACC, 'c1'), 'get', `/messenger/${ACC}/conversations/c1`],
  ['msg.userProfile', (m) => m.messengerApi.getUserProfile(ACC, 'p1'), 'get', `/messenger/${ACC}/user/p1`],
  ['msg.autoReply', (m) => m.messengerApi.getAutoReplyConfig(ACC), 'get', `/messenger/${ACC}/auto-reply`],
  ['msg.updateAutoReply', (m) => m.messengerApi.updateAutoReplyConfig(ACC, { enabled: true }), 'put', `/messenger/${ACC}/auto-reply`],
  ['msg.sidecar', (m) => m.messengerApi.getSidecarStatus(), 'get', '/messenger/sidecar/status'],
  ['msg.orchestrator', (m) => m.messengerApi.getOrchestratorStatus(), 'get', '/ops/browser-orchestrator'],
  ['msg.releaseLock', (m) => m.messengerApi.releaseOrchestratorLock(), 'post', '/ops/browser-orchestrator/release'],
  ['msg.personalConvs', (m) => m.messengerApi.getPersonalConversations(ACC), 'get', `/messenger/${ACC}/personal/conversations`],
  ['msg.personalMsgs', (m) => m.messengerApi.getPersonalMessages(ACC, 't1', true), 'get', `/messenger/${ACC}/personal/conversations/t1`],
  ['msg.personalSend', (m) => m.messengerApi.sendPersonalMessage(ACC, { thread_id: 't1', text: 'hi' }), 'post', `/messenger/${ACC}/personal/send`],
  ['msg.personalAutoReply', (m) => m.messengerApi.getPersonalAutoReply(ACC), 'get', `/messenger/${ACC}/personal/auto-reply`],
  ['msg.updatePersonalAutoReply', (m) => m.messengerApi.updatePersonalAutoReply(ACC, { enabled: false }), 'put', `/messenger/${ACC}/personal/auto-reply`],
  ['msg.createBot', (m) => m.messengerApi.createBot(ACC, { name: 'B' }), 'post', `/messenger/${ACC}/bot/create`],
  ['msg.getBot', (m) => m.messengerApi.getBot(ACC), 'get', `/messenger/${ACC}/bot`],
  ['msg.updateBot', (m) => m.messengerApi.updateBot(ACC, { enabled: true }), 'put', `/messenger/${ACC}/bot`],
  ['msg.activateBot', (m) => m.messengerApi.activateBot(ACC), 'post', `/messenger/${ACC}/bot/activate`],
  ['msg.deactivateBot', (m) => m.messengerApi.deactivateBot(ACC), 'post', `/messenger/${ACC}/bot/deactivate`],
  ['msg.pauseThread', (m) => m.messengerApi.pauseBotThread(ACC, 't1'), 'post', `/messenger/${ACC}/bot/pause-thread/t1`],
  ['msg.resumeThread', (m) => m.messengerApi.resumeBotThread(ACC, 't1'), 'post', `/messenger/${ACC}/bot/resume-thread/t1`],
  ['msg.personalities', (m) => m.messengerApi.getBotPersonalities(ACC), 'get', `/messenger/${ACC}/bot/personalities`],

  // --- whatsapp ---
  ['wa.setup', (m) => m.whatsappApi.setup(ACC), 'post', `/whatsapp/${ACC}/setup`],
  ['wa.updateCredentials', (m) => m.whatsappApi.updateCredentials(ACC, { access_token: 't', phone_number_id: 'p' }), 'put', `/whatsapp/${ACC}/credentials`],
  ['wa.setupStatus', (m) => m.whatsappApi.getSetupStatus(ACC), 'get', `/whatsapp/${ACC}/setup-status`],
  ['wa.getProfile', (m) => m.whatsappApi.getProfile(ACC), 'get', `/whatsapp/${ACC}/profile`],
  ['wa.updateProfile', (m) => m.whatsappApi.updateProfile(ACC, { about: 'a' }), 'put', `/whatsapp/${ACC}/profile`],
  ['wa.createNumber', (m) => m.whatsappApi.createNumber({ waba_id: 'w', cc: '30', phone_number: '1', verified_name: 'v' }), 'post', '/whatsapp/register/create-number'],
  ['wa.requestCode', (m) => m.whatsappApi.requestCode({ phone_number_id: 'p' }), 'post', '/whatsapp/register/request-code'],
  ['wa.verifyCode', (m) => m.whatsappApi.verifyCode({ phone_number_id: 'p', code: '1' }), 'post', '/whatsapp/register/verify-code'],
  ['wa.registerNumber', (m) => m.whatsappApi.registerNumber({ phone_number_id: 'p', pin: '1' }), 'post', '/whatsapp/register/number'],
  ['wa.deregister', (m) => m.whatsappApi.deregisterNumber({ phone_number_id: 'p' }), 'post', '/whatsapp/register/deregister'],
  ['wa.requestPhoneCode', (m) => m.whatsappApi.requestPhoneCode(ACC, {}), 'post', `/whatsapp/${ACC}/phone/request-code`],
  ['wa.verifyPhoneCode', (m) => m.whatsappApi.verifyPhoneCode(ACC, { code: '1' }), 'post', `/whatsapp/${ACC}/phone/verify-code`],
  ['wa.registerPhone', (m) => m.whatsappApi.registerPhone(ACC, { pin: '1' }), 'post', `/whatsapp/${ACC}/phone/register`],
  ['wa.phoneStatus', (m) => m.whatsappApi.getPhoneStatus(ACC), 'get', `/whatsapp/${ACC}/phone/status`],
  ['wa.subscribeHooks', (m) => m.whatsappApi.subscribeWebhooks({ waba_id: 'w' }), 'post', '/whatsapp/webhooks/subscribe'],
  ['wa.getHooks', (m) => m.whatsappApi.getWebhookSubscriptions('w'), 'get', '/whatsapp/webhooks/subscriptions/w'],
  ['wa.unsubscribeHooks', (m) => m.whatsappApi.unsubscribeWebhooks({ waba_id: 'w' }), 'delete', '/whatsapp/webhooks/subscribe'],
  ['wa.send', (m) => m.whatsappApi.sendMessage(ACC, { to: '30' }), 'post', `/whatsapp/${ACC}/send`],
  ['wa.sendTemplate', (m) => m.whatsappApi.sendTemplate(ACC, { to: '30', template_name: 't' }), 'post', `/whatsapp/${ACC}/send-template`],
  ['wa.autoReply', (m) => m.whatsappApi.getAutoReply(ACC), 'get', `/whatsapp/${ACC}/auto-reply`],
  ['wa.updateAutoReply', (m) => m.whatsappApi.updateAutoReply(ACC, { enabled: true }), 'put', `/whatsapp/${ACC}/auto-reply`],
  ['wa.createBot', (m) => m.whatsappApi.createBot(ACC, { name: 'b' }), 'post', `/whatsapp/${ACC}/bot/create`],
  ['wa.getBot', (m) => m.whatsappApi.getBot(ACC), 'get', `/whatsapp/${ACC}/bot`],
  ['wa.updateBot', (m) => m.whatsappApi.updateBot(ACC, {}), 'put', `/whatsapp/${ACC}/bot`],
  ['wa.activateBot', (m) => m.whatsappApi.activateBot(ACC), 'post', `/whatsapp/${ACC}/bot/activate`],
  ['wa.deactivateBot', (m) => m.whatsappApi.deactivateBot(ACC), 'post', `/whatsapp/${ACC}/bot/deactivate`],
  ['wa.personalities', (m) => m.whatsappApi.getBotPersonalities(ACC), 'get', `/whatsapp/${ACC}/bot/personalities`],
  ['wa.pauseThread', (m) => m.whatsappApi.pauseThread(ACC, { phone: '1' }), 'post', `/whatsapp/${ACC}/threads/pause`],
  ['wa.resumeThread', (m) => m.whatsappApi.resumeThread(ACC, { phone: '1' }), 'post', `/whatsapp/${ACC}/threads/resume`],
  ['wa.threadConfig', (m) => m.whatsappApi.getThreadConfig(ACC, '30'), 'get', `/whatsapp/${ACC}/threads/30/config`],
  ['wa.updateThreadConfig', (m) => m.whatsappApi.updateThreadConfig(ACC, '30', {}), 'put', `/whatsapp/${ACC}/threads/30/config`],
  ['wa.threadMemory', (m) => m.whatsappApi.getThreadMemory(ACC, '30'), 'get', `/whatsapp/${ACC}/threads/30/memory`],
  ['wa.threadWindow', (m) => m.whatsappApi.getThreadWindow(ACC, '30'), 'get', `/whatsapp/${ACC}/threads/30/window`],
  ['wa.indexBrand', (m) => m.whatsappApi.indexBrand(ACC), 'post', `/whatsapp/${ACC}/index-brand`],
  ['wa.listFlows', (m) => m.whatsappApi.listFlows(ACC), 'get', `/whatsapp/${ACC}/flows`],
  ['wa.getFlow', (m) => m.whatsappApi.getFlow(ACC, 'f1'), 'get', `/whatsapp/${ACC}/flows/f1`],
  ['wa.createFlow', (m) => m.whatsappApi.createFlow(ACC, { name: 'F', categories: [] }), 'post', `/whatsapp/${ACC}/flows`],
  ['wa.updateFlow', (m) => m.whatsappApi.updateFlow(ACC, 'f1', {}), 'put', `/whatsapp/${ACC}/flows/f1`],
  ['wa.updateFlowJson', (m) => m.whatsappApi.updateFlowJson(ACC, 'f1', {}), 'put', `/whatsapp/${ACC}/flows/f1/json`],
  ['wa.getFlowJson', (m) => m.whatsappApi.getFlowJson(ACC, 'f1'), 'get', `/whatsapp/${ACC}/flows/f1/json`],
  ['wa.validateFlow', (m) => m.whatsappApi.validateFlow(ACC, 'f1'), 'post', `/whatsapp/${ACC}/flows/f1/validate`],
  ['wa.publishFlow', (m) => m.whatsappApi.publishFlow(ACC, 'f1'), 'post', `/whatsapp/${ACC}/flows/f1/publish`],
  ['wa.deleteFlow', (m) => m.whatsappApi.deleteFlow(ACC, 'f1'), 'delete', `/whatsapp/${ACC}/flows/f1`],
  ['wa.sendFlow', (m) => m.whatsappApi.sendFlow(ACC, { to: '1', flow_id: 'f', flow_token: 't' }), 'post', `/whatsapp/${ACC}/flows/send`],
  ['wa.flowTemplates', (m) => m.whatsappApi.listFlowTemplates(ACC), 'get', `/whatsapp/${ACC}/flows/templates/list`],
  ['wa.flowFromTemplate', (m) => m.whatsappApi.createFlowFromTemplate(ACC, { template_id: 't', name: 'n' }), 'post', `/whatsapp/${ACC}/flows/from-template`],

  // --- telegram ---
  ['tg.connect', (m) => m.telegramApi.connect({ bot_token: 't' }), 'post', '/telegram/connect'],
  ['tg.updateCredentials', (m) => m.telegramApi.updateCredentials(ACC, { bot_token: 't' }), 'put', `/telegram/${ACC}/credentials`],
  ['tg.setupStatus', (m) => m.telegramApi.getSetupStatus(ACC), 'get', `/telegram/${ACC}/setup-status`],
  ['tg.setupWebhook', (m) => m.telegramApi.setupWebhook(ACC), 'post', `/telegram/${ACC}/setup-webhook`],
  ['tg.deleteWebhook', (m) => m.telegramApi.deleteWebhook(ACC), 'post', `/telegram/${ACC}/delete-webhook`],
  ['tg.send', (m) => m.telegramApi.sendMessage(ACC, { chat_id: 1, text: 't' }), 'post', `/telegram/${ACC}/send`],
  ['tg.autoReply', (m) => m.telegramApi.getAutoReply(ACC), 'get', `/telegram/${ACC}/auto-reply`],
  ['tg.updateAutoReply', (m) => m.telegramApi.updateAutoReply(ACC, { enabled: true }), 'put', `/telegram/${ACC}/auto-reply`],
  ['tg.createBot', (m) => m.telegramApi.createBot(ACC, { name: 'b' }), 'post', `/telegram/${ACC}/bot/create`],
  ['tg.getBot', (m) => m.telegramApi.getBot(ACC), 'get', `/telegram/${ACC}/bot`],
  ['tg.updateBot', (m) => m.telegramApi.updateBot(ACC, {}), 'put', `/telegram/${ACC}/bot`],
  ['tg.activateBot', (m) => m.telegramApi.activateBot(ACC), 'post', `/telegram/${ACC}/bot/activate`],
  ['tg.deactivateBot', (m) => m.telegramApi.deactivateBot(ACC), 'post', `/telegram/${ACC}/bot/deactivate`],
  ['tg.pauseThread', (m) => m.telegramApi.pauseThread(ACC, { chat_id: '1' }), 'post', `/telegram/${ACC}/threads/pause`],
  ['tg.resumeThread', (m) => m.telegramApi.resumeThread(ACC, { chat_id: '1' }), 'post', `/telegram/${ACC}/threads/resume`],
  ['tg.groupWatch', (m) => m.telegramApi.getGroupWatch(ACC), 'get', `/telegram/${ACC}/group-watch`],
  ['tg.updateGroupWatch', (m) => m.telegramApi.updateGroupWatch(ACC, {}), 'put', `/telegram/${ACC}/group-watch`],
  ['tg.addWatchedChat', (m) => m.telegramApi.addWatchedChat(ACC, { chat_id: '1' }), 'post', `/telegram/${ACC}/group-watch/add-chat`],
  ['tg.digestNow', (m) => m.telegramApi.digestNow(ACC), 'post', `/telegram/${ACC}/group-watch/digest-now`],
  ['tg.groupWatchActivity', (m) => m.telegramApi.getGroupWatchActivity(ACC), 'get', `/telegram/${ACC}/group-watch/activity`],
  ['tg.setupLinks', (m) => m.telegramApi.setupGroupWatchLinks(ACC), 'post', `/telegram/${ACC}/group-watch/setup-links`],
  ['tg.listChannels', (m) => m.telegramApi.listChannels(ACC), 'get', `/telegram/${ACC}/channels`],
  ['tg.chatInfo', (m) => m.telegramApi.getChatInfo(ACC, '1'), 'get', `/telegram/${ACC}/chat-info`],
  ['tg.chatAdmins', (m) => m.telegramApi.getChatAdmins(ACC, '1'), 'get', `/telegram/${ACC}/chat-admins`],
  ['tg.setDescription', (m) => m.telegramApi.setChatDescription(ACC, { chat_id: '1', description: 'd' }), 'put', `/telegram/${ACC}/chat-description`],
  ['tg.inviteLink', (m) => m.telegramApi.createInviteLink(ACC, { chat_id: '1' }), 'post', `/telegram/${ACC}/invite-link`],
  ['tg.pin', (m) => m.telegramApi.pinMessage(ACC, { chat_id: '1', message_id: 2 }), 'post', `/telegram/${ACC}/pin`],
  ['tg.channelConfig', (m) => m.telegramApi.getChannelConfig(ACC), 'get', `/telegram/${ACC}/channel-config`],
  ['tg.setChannelConfig', (m) => m.telegramApi.setChannelConfig(ACC, { chat_id: '1' }), 'put', `/telegram/${ACC}/channel-config`],
  ['tg.notifyConfig', (m) => m.telegramApi.getNotifyConfig(ACC), 'get', `/telegram/${ACC}/notify-config`],

  // --- publishing ---
  ['pub.listQueue', (m) => m.publishingApi.listQueue({ status: 'pending' }), 'get', '/publishing/queue'],
  ['pub.getItem', (m) => m.publishingApi.getQueueItem(ID), 'get', `/publishing/queue/${ID}`],
  ['pub.retry', (m) => m.publishingApi.retryQueueItem(ID), 'post', `/publishing/queue/${ID}/retry`],
  ['pub.cancel', (m) => m.publishingApi.cancelQueueItem(ID), 'post', `/publishing/queue/${ID}/cancel`],
  ['pub.history', (m) => m.publishingApi.getHistory({ page: 2 }), 'get', '/publishing/history'],

  // --- analytics ---
  ['an.overview', (m) => m.analyticsApi.getOverview({ days: 7 }), 'get', '/analytics/overview'],
  ['an.platforms', (m) => m.analyticsApi.getPlatformMetrics(), 'get', '/analytics/platforms'],
  ['an.postMetrics', (m) => m.analyticsApi.getPostAnalytics('p1'), 'get', '/analytics/posts/p1/metrics'],
  ['an.engagement', (m) => m.analyticsApi.getEngagementTrends(), 'get', '/analytics/engagement'],
  ['an.followers', (m) => m.analyticsApi.getFollowerGrowth(), 'get', '/analytics/followers'],
  ['an.topPosts', (m) => m.analyticsApi.getTopPosts({ limit: 5 }), 'get', '/analytics/top-posts'],
  ['an.export', (m) => m.analyticsApi.exportReport({ format: 'csv' }), 'get', '/analytics/reports/export'],
  ['an.sync', (m) => m.analyticsApi.syncFromPlatforms(), 'post', '/analytics/sync'],
  ['an.snapshots', (m) => m.analyticsApi.listSnapshots(), 'get', '/analytics/snapshots'],
  ['an.botSummary', (m) => m.analyticsApi.getBotSummary(), 'get', '/analytics/bots/summary'],
  ['an.cfAI', (m) => m.analyticsApi.getCloudflareAIUsage(), 'get', '/analytics/bots/cloudflare-ai'],
  ['an.cfOverview', (m) => m.analyticsApi.getCloudflareOverview(), 'get', '/analytics/bots/cloudflare-overview'],
  ['an.pipeline', (m) => m.analyticsApi.getPublishPipeline(), 'get', '/analytics/pipeline'],
  ['an.adCampaigns', (m) => m.analyticsApi.getAdCampaigns(), 'get', '/analytics/ad-campaigns'],
  ['an.initiatives', (m) => m.analyticsApi.getInitiatives(), 'get', '/analytics/initiatives'],
  ['an.initiativeEvent', (m) => m.analyticsApi.recordInitiativeEvent({ event_type: 'send' }), 'post', '/analytics/initiative-events'],

  // --- ai providers + ai ---
  ['aip.catalog', (m) => m.aiProvidersApi.getCatalog(), 'get', '/ai-providers/catalog'],
  ['aip.list', (m) => m.aiProvidersApi.list(), 'get', '/ai-providers'],
  ['aip.upsert', (m) => m.aiProvidersApi.upsert('openai', {}), 'put', '/ai-providers/openai'],
  ['aip.delete', (m) => m.aiProvidersApi.delete('openai'), 'delete', '/ai-providers/openai'],
  ['aip.test', (m) => m.aiProvidersApi.test('openai'), 'post', '/ai-providers/openai/test'],
  ['aip.models', (m) => m.aiProvidersApi.listModels('openai'), 'get', '/ai-providers/openai/models'],
  ['aip.usage', (m) => m.aiProvidersApi.getUsage(), 'get', '/ai-providers/usage'],
  ['aip.resetCircuit', (m) => m.aiProvidersApi.resetCircuit('openai'), 'post', '/ai-providers/openai/reset-circuit'],
  ['ai.generate', (m) => m.aiApi.generateContent({ prompt: 'p', platform: 'x' }), 'post', '/ai/generate-content'],
  ['ai.improve', (m) => m.aiApi.improveContent({ content: 'c', platform: 'x' }), 'post', '/ai/improve-content'],
  ['ai.hashtags', (m) => m.aiApi.generateHashtags({ content: 'c', platform: 'x' }), 'post', '/ai/generate-hashtags'],
  ['ai.imagePrompt', (m) => m.aiApi.generateImagePrompt({ description: 'd' }), 'post', '/ai/generate-image-prompt'],
  ['ai.analyze', (m) => m.aiApi.analyzeContent({ content: 'c', platform: 'x' }), 'post', '/ai/analyze-content'],
  ['ai.carousel', (m) => m.aiApi.generateCarousel({ topic: 't' }), 'post', '/ai/generate-carousel'],
  ['ai.carouselPipeline', (m) => m.aiApi.generateCarouselPipeline({ topic: 't' }), 'post', '/ai/generate-carousel-pipeline'],
  ['ai.enhancePrompt', (m) => m.aiApi.enhanceImagePrompt('d'), 'post', '/ai/generate-image-prompt'],
  ['ai.dmrStatus', (m) => m.aiApi.getDmrStatus(), 'get', '/ai/dmr/status'],
  ['ai.autoConfigure', (m) => m.aiApi.autoConfigurePrompt('p'), 'post', '/ai/auto-configure'],
  ['ai.saveTemplate', (m) => m.aiApi.saveGenerationTemplate({ name: 'n', prompt_template: 'p' }), 'post', '/ai/save-generation-template'],
  ['ai.workflowConfig', (m) => m.aiApi.getWorkflowConfig('post'), 'get', '/ai/workflow-config/post'],
  ['ai.seedWorkflows', (m) => m.aiApi.seedDefaultWorkflows(), 'post', '/ai/seed-default-workflows'],
  ['ai.transcribe', (m) => m.aiApi.transcribeAudio(FILE, 'm1'), 'post', '/ai/transcribe'],
  ['ai.batchSubmit', (m) => m.aiApi.submitBatchInference({ model: 'm', requests: [] }), 'post', '/ai/workers-ai/batch'],
  ['ai.batchRetrieve', (m) => m.aiApi.retrieveBatchInference({ model: 'm', request_id: 'r' }), 'post', '/ai/workers-ai/batch/retrieve'],
  ['ai.seo', (m) => m.aiApi.analyzeSeo({ content: 'c' }), 'post', '/ai/seo'],
  ['ai.nlp', (m) => m.aiApi.nlpCheck({ content: 'c' }), 'post', '/ai/nlp-check'],
  ['ai.spellcheck', (m) => m.aiApi.spellcheck('text'), 'post', '/ai/spellcheck'],

  // --- linkedin ---
  ['li.genPost', (m) => m.linkedinApi.generatePost({ topic: 't' }), 'post', '/linkedin/generate-post'],
  ['li.genArticle', (m) => m.linkedinApi.generateArticle({ topic: 't' }), 'post', '/linkedin/generate-article'],
  ['li.genHashtags', (m) => m.linkedinApi.generateHashtags({ content: 'c' }), 'post', '/linkedin/generate-hashtags'],
  ['li.improve', (m) => m.linkedinApi.improvePost({ content: 'c' }), 'post', '/linkedin/improve-post'],
  ['li.genComment', (m) => m.linkedinApi.generateComment({ post_text: 'p' }), 'post', '/linkedin/generate-comment'],
  ['li.bestTime', (m) => m.linkedinApi.bestTime('personal'), 'get', '/linkedin/best-time'],
  ['li.validate', (m) => m.linkedinApi.validateAccount(ACC), 'get', `/linkedin/accounts/${ACC}/validate`],
  ['li.followers', (m) => m.linkedinApi.followers(ACC), 'get', `/linkedin/accounts/${ACC}/followers`],
  ['li.postAnalytics', (m) => m.linkedinApi.postAnalytics('urn:li:1', ACC), 'get', `/linkedin/analytics/post/${encodeURIComponent('urn:li:1')}`],
  ['li.orgAnalytics', (m) => m.linkedinApi.organizationAnalytics(ACC), 'get', '/linkedin/analytics/organization'],
  ['li.publish', (m) => m.linkedinApi.publish({ account_id: ACC, commentary: 'c' }), 'post', '/linkedin/publish'],
  ['li.comment', (m) => m.linkedinApi.comment({ account_id: ACC, post_urn: 'u', text: 't' }), 'post', '/linkedin/comment'],
  ['li.companyUrl', (m) => m.linkedinApi.companyPageUrl(ACC), 'get', '/linkedin/company-page-url'],

  // --- brand ---
  ['brand.get', (m) => m.brandApi.get(), 'get', '/brand'],
  ['brand.create', (m) => m.brandApi.create({ name: 'B' }), 'post', '/brand'],
  ['brand.update', (m) => m.brandApi.update({ name: 'B' }), 'put', '/brand'],
  ['brand.delete', (m) => m.brandApi.delete(), 'delete', '/brand'],
  ['brand.getVoice', (m) => m.brandApi.getVoice(), 'get', '/brand/voice'],
  ['brand.updateVoice', (m) => m.brandApi.updateVoice({}), 'put', '/brand/voice'],
  ['brand.getVisual', (m) => m.brandApi.getVisual(), 'get', '/brand/visual'],
  ['brand.updateVisual', (m) => m.brandApi.updateVisual({}), 'put', '/brand/visual'],
  ['brand.guidelines', (m) => m.brandApi.getGuidelines(), 'get', '/brand/guidelines'],
  ['brand.guidelinesByToken', (m) => m.brandApi.getGuidelinesByToken('tok'), 'get', '/brand/guidelines/share/tok'],
  ['brand.compile', (m) => m.brandApi.compileGuidelines(), 'post', '/brand/guidelines/compile'],
  ['brand.assets', (m) => m.brandApi.listAssets(), 'get', '/brand/assets'],
  ['brand.createAsset', (m) => m.brandApi.createAsset({ name: 'A' }), 'post', '/brand/assets'],
  ['brand.deleteAsset', (m) => m.brandApi.deleteAsset(ID), 'delete', `/brand/assets/${ID}`],
  ['brand.extract', (m) => m.brandApi.extractFromUrl({ url: 'https://x' }), 'post', '/brand/extract'],
  ['brand.analyzeVoice', (m) => m.brandApi.analyzeVoice({ samples: ['s'] }), 'post', '/brand/analyze-voice'],
  ['brand.compliance', (m) => m.brandApi.scoreCompliance({ content: 'c' }), 'post', '/brand/compliance'],
  ['brand.genLogo', (m) => m.brandApi.generateLogo({}), 'post', '/brand/generate-logo'],
  ['brand.genFavicon', (m) => m.brandApi.generateFavicon(), 'post', '/brand/generate-favicon'],
  ['brand.mentions', (m) => m.brandApi.listMentions(), 'get', '/brand/mentions'],
  ['brand.collectMentions', (m) => m.brandApi.collectMentions(), 'post', '/brand/mentions/collect'],
  ['brand.competitors', (m) => m.brandApi.listCompetitors(), 'get', '/brand/competitors'],
  ['brand.snapshot', (m) => m.brandApi.snapshotCompetitor({ competitor_name: 'c' }), 'post', '/brand/competitors/snapshot'],
  ['brand.health', (m) => m.brandApi.getHealth(), 'get', '/brand/health'],
  ['brand.autopilot', (m) => m.brandApi.runAutopilot({ days: 3 }), 'post', '/brand/autopilot/run'],
  ['brand.trends', (m) => m.brandApi.getTrends(), 'get', '/brand/trends'],
  ['brand.digitalCard', (m) => m.brandApi.getDigitalCard(), 'get', '/brand/digital-card'],
  ['brand.digitalCardPublic', (m) => m.brandApi.getDigitalCardPublic('t'), 'get', '/brand/digital-card/t'],

  // --- digital cards ---
  ['dc.list', (m) => m.digitalCardApi.list(), 'get', '/digital-cards'],
  ['dc.get', (m) => m.digitalCardApi.get(ID), 'get', `/digital-cards/${ID}`],
  ['dc.create', (m) => m.digitalCardApi.create({}), 'post', '/digital-cards'],
  ['dc.fromBrand', (m) => m.digitalCardApi.createFromBrand(), 'post', '/digital-cards/from-brand'],
  ['dc.update', (m) => m.digitalCardApi.update(ID, {}), 'patch', `/digital-cards/${ID}`],
  ['dc.delete', (m) => m.digitalCardApi.delete(ID), 'delete', `/digital-cards/${ID}`],
  ['dc.vcard', (m) => m.digitalCardApi.downloadVCard(ID), 'get', `/digital-cards/${ID}/vcard`],
  ['dc.public', (m) => m.digitalCardApi.getPublic('tok'), 'get', '/digital-cards/public/tok'],
  ['dc.track', (m) => m.digitalCardApi.trackAction('tok', 'save'), 'post', '/digital-cards/public/tok/track'],
  ['dc.send', (m) => m.digitalCardApi.send(ID, { platform: 'whatsapp' }), 'post', `/digital-cards/${ID}/send`],

  // --- media enhance ---
  ['me.presets', (m) => m.mediaEnhanceApi.getPresets(), 'get', '/media/enhance/presets'],
  ['me.info', (m) => m.mediaEnhanceApi.getInfo(ID), 'get', `/media/enhance/assets/${ID}/info`],
  ['me.quality', (m) => m.mediaEnhanceApi.getQuality(ID), 'get', `/media/enhance/assets/${ID}/quality`],
  ['me.resize', (m) => m.mediaEnhanceApi.resize(ID, {}), 'post', `/media/enhance/assets/${ID}/resize`],
  ['me.crop', (m) => m.mediaEnhanceApi.crop(ID, { x: 0, y: 0, width: 1, height: 1 }), 'post', `/media/enhance/assets/${ID}/crop`],
  ['me.convert', (m) => m.mediaEnhanceApi.convert(ID, { format: 'png' }), 'post', `/media/enhance/assets/${ID}/convert`],
  ['me.compress', (m) => m.mediaEnhanceApi.compress(ID, {}), 'post', `/media/enhance/assets/${ID}/compress`],
  ['me.watermark', (m) => m.mediaEnhanceApi.watermark(ID, { text: 'w' }), 'post', `/media/enhance/assets/${ID}/watermark`],
  ['me.upscale', (m) => m.mediaEnhanceApi.upscale(ID, { scale: 2 }), 'post', `/media/enhance/assets/${ID}/upscale`],
  ['me.removeBg', (m) => m.mediaEnhanceApi.removeBackground(ID), 'post', `/media/enhance/assets/${ID}/remove-background`],
  ['me.smartCrop', (m) => m.mediaEnhanceApi.smartCrop(ID, { target_width: 1, target_height: 1 }), 'post', `/media/enhance/assets/${ID}/smart-crop`],
  ['me.altText', (m) => m.mediaEnhanceApi.generateAltText(ID), 'post', `/media/enhance/assets/${ID}/alt-text`],
  ['me.batch', (m) => m.mediaEnhanceApi.batch({ asset_ids: ['1'], operation: 'resize' }), 'post', '/media/enhance/batch'],

  // --- audit / inbox / reports / ops ---
  ['audit.list', (m) => m.auditApi.list({ action: 'login' }), 'get', '/audit/audit-logs'],
  ['inbox.get', (m) => m.inboxApi.getInbox(), 'get', '/inbox/inbox'],
  ['reports.notebooks', (m) => m.reportsApi.listNotebooks(), 'get', '/reports/notebooks'],
  ['reports.list', (m) => m.reportsApi.list({ limit: 5 }), 'get', '/reports'],
  ['reports.getFile', (m) => m.reportsApi.getFile('a b.html'), 'get', `/reports/files/${encodeURIComponent('a b.html')}`],
  ['reports.run', (m) => m.reportsApi.run('nb'), 'post', '/reports/run'],
  ['ops.console', (m) => m.opsApi.getConsole(), 'get', '/ops/console'],
  ['ops.orchestrator', (m) => m.opsApi.getBrowserOrchestrator(), 'get', '/ops/browser-orchestrator'],
  ['ops.releaseLock', (m) => m.opsApi.releaseBrowserLock(), 'post', '/ops/browser-orchestrator/release'],
  ['ops.sessionHeal', (m) => m.opsApi.sessionHeal(), 'post', '/ops/session-heal'],
  ['ops.tiktokAudit', (m) => m.opsApi.updateTikTokAudit({ status: 'ok' }), 'put', '/ops/tiktok-audit'],
  ['ops.digest', (m) => m.opsApi.triggerDigest({ days: 1 }), 'post', '/ops/daily-digest'],

  // --- auth extras ---
  ['auth.setup2FA', (m) => m.authApi.setup2FA(), 'post', '/auth/2fa/setup'],
  ['auth.verify2FA', (m) => m.authApi.verify2FA('123'), 'post', '/auth/2fa/verify'],
  ['auth.disable2FA', (m) => m.authApi.disable2FA('pw'), 'delete', '/auth/2fa'],
  ['auth.notifPrefs', (m) => m.authApi.getNotificationPreferences(), 'get', '/auth/notifications/preferences'],
  ['auth.updateNotifPrefs', (m) => m.authApi.updateNotificationPreferences({
    email_new_post: true, email_scheduled: true, email_analytics: true,
    email_on_quota: true, email_on_invite: true, email_account_connected: true,
    push_new_post: true, push_scheduled: true,
  }), 'put', '/auth/notifications/preferences'],
  ['auth.export', (m) => m.authApi.exportData(), 'get', '/auth/export-data'],
  ['auth.deleteAccount', (m) => m.authApi.deleteAccount('pw'), 'delete', '/auth/account'],
  ['auth.switchTeam', (m) => m.authApi.switchTeam('t1'), 'post', '/auth/switch-team'],

  // --- email campaigns + CRM ---
  ['email.list', (m) => m.emailCampaignsApi.list(), 'get', '/email-campaigns'],
  ['email.get', (m) => m.emailCampaignsApi.get(ID), 'get', `/email-campaigns/${ID}`],
  ['email.create', (m) => m.emailCampaignsApi.create({ name: 'N', kind: 'survey', subject: 'S', text_body: 'T' }), 'post', '/email-campaigns'],
  ['email.addAudience', (m) => m.emailCampaignsApi.addAudience(ID, { include_espocrm: true }), 'post', `/email-campaigns/${ID}/audience`],
  ['email.send', (m) => m.emailCampaignsApi.send(ID), 'post', `/email-campaigns/${ID}/send`],
  ['email.delete', (m) => m.emailCampaignsApi.delete(ID), 'delete', `/email-campaigns/${ID}`],
  ['email.recipients', (m) => m.emailCampaignsApi.recipients(ID), 'get', `/email-campaigns/${ID}/recipients`],
  ['email.responses', (m) => m.emailCampaignsApi.responses(ID), 'get', `/email-campaigns/${ID}/responses`],
  ['email.suppressions', (m) => m.emailCampaignsApi.suppressions(), 'get', '/email-campaigns/suppressions/list'],
  ['email.addSuppression', (m) => m.emailCampaignsApi.addSuppression('a@b.c', 'manual'), 'post', '/email-campaigns/suppressions/list'],
  ['email.crmSummary', (m) => m.emailCampaignsApi.crmSummary(), 'get', '/email-campaigns/crm/summary'],
  ['email.crmClients', (m) => m.emailCampaignsApi.crmClients(), 'get', '/email-campaigns/crm/clients'],
]

describe('API endpoint surface', () => {
  let apiModule: any
  let mockAxiosInstance: any

  beforeEach(async () => {
    vi.resetModules()
    vi.clearAllMocks()
    localStorageMock.getItem.mockReturnValue(null)

    const mockCall = vi.fn()
    mockAxiosInstance = Object.assign(mockCall, {
      defaults: { baseURL: '/api/v1' },
      interceptors: {
        request: { use: vi.fn() },
        response: { use: vi.fn() },
      },
      get: vi.fn().mockResolvedValue({ data: {} }),
      post: vi.fn().mockResolvedValue({ data: {} }),
      patch: vi.fn().mockResolvedValue({ data: {} }),
      put: vi.fn().mockResolvedValue({ data: {} }),
      delete: vi.fn().mockResolvedValue({ data: {} }),
    })

    mockedAxios.create.mockReturnValue(mockAxiosInstance)
    apiModule = await import('@/services/api')
  })

  afterEach(() => {
    vi.resetAllMocks()
  })

  it.each(CASES)('%s calls %s %s', async (_label, invoke, verb, url) => {
    await invoke(apiModule)
    const mock = mockAxiosInstance[verb] as ReturnType<typeof vi.fn>
    expect(mock, `${_label} should call .${verb}`).toHaveBeenCalled()
    expect(mock.mock.calls[0][0]).toBe(url)
  })
})

describe('API argument transforms', () => {
  let apiModule: any
  let mockAxiosInstance: any

  beforeEach(async () => {
    vi.resetModules()
    vi.clearAllMocks()
    localStorageMock.getItem.mockReturnValue(null)

    const mockCall = vi.fn()
    mockAxiosInstance = Object.assign(mockCall, {
      defaults: { baseURL: '/api/v1' },
      interceptors: {
        request: { use: vi.fn() },
        response: { use: vi.fn() },
      },
      get: vi.fn().mockResolvedValue({ data: {} }),
      post: vi.fn().mockResolvedValue({ data: {} }),
      patch: vi.fn().mockResolvedValue({ data: {} }),
      put: vi.fn().mockResolvedValue({ data: {} }),
      delete: vi.fn().mockResolvedValue({ data: {} }),
    })

    mockedAxios.create.mockReturnValue(mockAxiosInstance)
    apiModule = await import('@/services/api')
  })

  afterEach(() => {
    vi.resetAllMocks()
  })

  it('mediaUrl passes through absolute URLs and view paths', () => {
    expect(apiModule.mediaUrl('https://cdn.x/a.png')).toBe('https://cdn.x/a.png')
    expect(apiModule.mediaUrl('/api/v1/media/view?path=x')).toBe('/api/v1/media/view?path=x')
    expect(apiModule.mediaUrl('storage/a b.png')).toBe(
      `/api/v1/media/view?path=${encodeURIComponent('storage/a b.png')}`
    )
    expect(apiModule.mediaUrl(null)).toBe('')
    expect(apiModule.mediaUrl(undefined)).toBe('')
  })

  it('emailCampaigns forwards bodies and CRM limit params', async () => {
    await apiModule.emailCampaignsApi.addSuppression('x@y.z', 'bounce')
    expect(mockAxiosInstance.post).toHaveBeenLastCalledWith(
      '/email-campaigns/suppressions/list', { email: 'x@y.z', reason: 'bounce' })
    await apiModule.emailCampaignsApi.addAudience('c1', { emails: [{ email: 'a@b.c' }], lead_sources: ['web'] })
    expect(mockAxiosInstance.post).toHaveBeenLastCalledWith(
      '/email-campaigns/c1/audience', { emails: [{ email: 'a@b.c' }], lead_sources: ['web'] })
    await apiModule.emailCampaignsApi.crmClients()
    expect(mockAxiosInstance.get).toHaveBeenLastCalledWith(
      '/email-campaigns/crm/clients', { params: { limit: 100 } })
    await apiModule.emailCampaignsApi.crmClients(25)
    expect(mockAxiosInstance.get).toHaveBeenLastCalledWith(
      '/email-campaigns/crm/clients', { params: { limit: 25 } })
  })

  it('createPost maps targets[] to target_account_ids', async () => {
    await apiModule.contentApi.createPost({
      content_text: 'hi',
      targets: [{ social_account_id: 's1' }, { social_account_id: 's2' }],
    })
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/content/posts', {
      content_text: 'hi',
      target_account_ids: ['s1', 's2'],
    })
  })

  it('createPost prefers explicit target_account_ids over targets', async () => {
    await apiModule.contentApi.createPost({
      target_account_ids: ['a'],
      targets: [{ social_account_id: 's1' }],
    })
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/content/posts', {
      target_account_ids: ['a'],
    })
  })

  it('listPosts strips empty status/platform params', async () => {
    await apiModule.contentApi.listPosts({ status: '', page: 2 })
    expect(mockAxiosInstance.get).toHaveBeenCalledWith('/content/posts', {
      params: { page: 2 },
    })
  })

  it('mediaApi.list strips falsy filter params', async () => {
    await apiModule.mediaApi.list({ page: 1, type: '', search: '' })
    expect(mockAxiosInstance.get).toHaveBeenCalledWith('/media/assets', {
      params: { page: 1 },
    })
  })

  it('improveContent falls back to instruction then engagement', async () => {
    await apiModule.aiApi.improveContent({ content: 'c', platform: 'x', instruction: 'shorten' })
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/ai/improve-content', {
      content: 'c', platform: 'x', goal: 'shorten',
    })
    await apiModule.aiApi.improveContent({ content: 'c', platform: 'x' })
    expect(mockAxiosInstance.post).toHaveBeenLastCalledWith('/ai/improve-content', {
      content: 'c', platform: 'x', goal: 'engagement',
    })
  })

  it('generateHashtags prefers max_hashtags over count over default', async () => {
    await apiModule.aiApi.generateHashtags({ content: 'c', platform: 'x', count: 3 })
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/ai/generate-hashtags', {
      content: 'c', platform: 'x', max_hashtags: 3,
    })
    await apiModule.aiApi.generateHashtags({ content: 'c', platform: 'x' })
    expect(mockAxiosInstance.post).toHaveBeenLastCalledWith('/ai/generate-hashtags', {
      content: 'c', platform: 'x', max_hashtags: 10,
    })
  })

  it('enhanceImagePrompt defaults style to photorealistic', async () => {
    await apiModule.aiApi.enhanceImagePrompt('sunset')
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/ai/generate-image-prompt', {
      description: 'sunset', style: 'photorealistic',
    })
  })

  it('generateImage omits width/height when not provided', async () => {
    await apiModule.mediaApi.generateImage('p')
    const body = mockAxiosInstance.post.mock.calls[0][1]
    expect(body).not.toHaveProperty('width')
    expect(body).not.toHaveProperty('height')
    expect(body.provider).toBe('cloudflare')
    expect(body.steps).toBe(4)
  })

  it('approvePost/rejectPost omit body when not provided', async () => {
    await apiModule.contentApi.approvePost('p1')
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/content/posts/p1/approve', undefined)
    await apiModule.contentApi.approvePost('p1', 'lgmt')
    expect(mockAxiosInstance.post).toHaveBeenLastCalledWith('/content/posts/p1/approve', { body: 'lgmt' })
  })

  it('checkout includes discount_code only when provided', async () => {
    await apiModule.billingApi.checkout('pro')
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/billing/checkout', { tier: 'pro' })
    await apiModule.billingApi.checkout('pro', 'SAVE10')
    expect(mockAxiosInstance.post).toHaveBeenLastCalledWith('/billing/checkout', {
      tier: 'pro', discount_code: 'SAVE10',
    })
  })

  it('deleteProfileFields joins field list into params', async () => {
    await apiModule.messengerApi.deleteProfileFields('a1', ['greeting', 'get_started'])
    expect(mockAxiosInstance.delete).toHaveBeenCalledWith('/messenger/a1/profile', {
      params: { fields: 'greeting,get_started' },
    })
  })

  it('snapshotCompetitor sends params with null body', async () => {
    await apiModule.brandApi.snapshotCompetitor({ competitor_name: 'acme', platform: 'x' })
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/brand/competitors/snapshot', null, {
      params: { competitor_name: 'acme', platform: 'x' },
    })
  })

  it('syncFromPlatforms defaults to 365-day async sync', async () => {
    await apiModule.analyticsApi.syncFromPlatforms()
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/analytics/sync', {
      days: 365, async_mode: true,
    })
  })

  it('reportsApi.fileUrl encodes and does not hit the network', () => {
    const url = apiModule.reportsApi.fileUrl('my report.html')
    expect(url).toBe(`/api/v1/reports/files/${encodeURIComponent('my report.html')}`)
    expect(mockAxiosInstance.get).not.toHaveBeenCalled()
  })

  it('reportsApi.run sends defaults', async () => {
    await apiModule.reportsApi.run('nb1')
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/reports/run', {
      notebook: 'nb1', parameters: {}, send: false,
    })
  })

  it('triggerDigest always enables async_queue', async () => {
    await apiModule.opsApi.triggerDigest({ days: 3 })
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/ops/daily-digest', null, {
      params: { async_queue: true, days: 3 },
    })
  })

  it('messenger setup defaults to empty object', async () => {
    await apiModule.messengerApi.setup('a1')
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/messenger/a1/setup', {})
  })

  it('whatsapp unsubscribeWebhooks passes waba_id as params', async () => {
    await apiModule.whatsappApi.unsubscribeWebhooks({ waba_id: 'w1' })
    expect(mockAxiosInstance.delete).toHaveBeenCalledWith('/whatsapp/webhooks/subscribe', {
      params: { waba_id: 'w1' },
    })
  })

  it('threads listPosts defaults limit to 25', async () => {
    await apiModule.threadsApi.listPosts('a1')
    expect(mockAxiosInstance.get).toHaveBeenCalledWith('/threads/posts', {
      params: { account_id: 'a1', limit: 25, after: undefined },
    })
  })

  it('transcribeAudio builds FormData with optional model', async () => {
    await apiModule.aiApi.transcribeAudio(new File(['a'], 'a.mp3'))
    const form = mockAxiosInstance.post.mock.calls[0][1] as FormData
    expect(form.get('file')).toBeInstanceOf(File)
    expect(form.get('model')).toBeNull()
  })
})
