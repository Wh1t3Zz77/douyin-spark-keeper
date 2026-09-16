'use client';

import { useEffect, useRef, useState, type SyntheticEvent } from 'react';
import {
  Ban,
  Bell,
  CalendarClock,
  Check,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Clock3,
  Copy,
  Crown,
  Eye,
  EyeOff,
  Flame,
  Gauge,
  History,
  Home,
  KeyRound,
  LogOut,
  Mail,
  Menu,
  MoreHorizontal,
  Plus,
  QrCode,
  RefreshCw,
  Settings,
  ShieldCheck,
  Sparkles,
  TicketCheck,
  Trash2,
  Users,
  X,
} from 'lucide-react';
import { Avatar, AvatarBadge, AvatarFallback } from '@/components/ui/avatar';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';

const nav = [
  { label: '总览', icon: Home },
  { label: '账号绑定', icon: QrCode },
  { label: '火花好友', icon: Users },
  { label: '执行计划', icon: CalendarClock },
  { label: '运行记录', icon: History },
  { label: '邮件通知', icon: Mail },
  { label: '升级 Pro', icon: Crown },
];

const API_BASE =
  process.env.NODE_ENV === 'development' ? 'http://localhost:8000' : '';

function SiteFooter() {
  return (
    <footer className="px-4 py-5 text-center text-[11px] leading-5 text-[#8e887f]">
      <span>火花值守 · 社区自托管版</span>
    </footer>
  );
}

type ProPeriod = 'week' | 'month' | 'quarter' | 'year';
type UserInfo = {
  id: string;
  username: string;
  email: string;
  is_admin: boolean;
  plan_tier: 'free' | 'pro' | 'expired';
  plan_period?: ProPeriod | null;
  plan_started_at?: string | null;
  plan_expires_at?: string | null;
  plan_remaining_days?: number | null;
  limits: {
    accounts: number;
    tasks_per_account: number;
    tasks_total: number;
    recipients_per_task: number;
    recipients_total: number;
  };
};
type DouyinAccount = {
  id: string;
  name: string;
  status: string;
  bound_at?: string | null;
  last_verified_at?: string | null;
};
type Binding = {
  id: string;
  status: string;
  qr_data_url?: string | null;
  error_message?: string | null;
  expires_at?: string | null;
  remaining_seconds?: number;
};
type NotificationPrefs = {
  enabled: boolean;
  login_expired: boolean;
  task_failed: boolean;
  security_challenge: boolean;
  daily_incomplete: boolean;
  daily_summary: boolean;
};
type ConversationType = 'friend' | 'group' | 'unknown';
type RecipientInfo = {
  id: string;
  contact_id?: string | null;
  contact_name: string;
  conversation_type: ConversationType;
  message: string;
  enabled: boolean;
};
type TaskInfo = {
  id: string;
  account_id: string;
  name: string;
  send_time: string;
  enabled: boolean;
  next_run_at: string;
  recipients: RecipientInfo[];
};
type RunItemInfo = {
  contact_name: string;
  message: string;
  status: string;
  reason?: string | null;
};
type RunInfo = {
  id: string;
  task_id: string;
  account_id?: string | null;
  task_name: string;
  status: string;
  attempt: number;
  scheduled_for: string;
  started_at?: string | null;
  finished_at?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  next_retry_at?: string | null;
  items: RunItemInfo[];
};
type InviteInfo = {
  id: string;
  max_uses: number;
  uses: number;
  active: boolean;
  expired: boolean;
  expires_at?: string | null;
  created_at: string;
};
type QueueInfo = {
  position: number | null;
  waiting_minutes: number;
  queued: number;
  running: boolean;
};
type ContactInfo = {
  id: string;
  display_name: string;
  conversation_type: ConversationType;
  type_locked?: boolean;
  avatar_url?: string | null;
  streak_days?: number | null;
  selected: boolean;
  message: string;
  last_seen_at: string;
};
type RecipientDraft = {
  contact_id: string;
  contact_name: string;
  conversation_type: ConversationType;
  message: string;
};
type ContactSyncInfo = {
  id: string;
  status: string;
  contacts: ContactInfo[];
  error_message?: string | null;
  finished_at?: string | null;
};
type ScheduleInfo = {
  send_time: string;
  nearby_tasks: number;
  capacity: number;
  available: boolean;
  level: 'idle' | 'light' | 'busy' | 'full';
  estimated_wait_minutes: number;
};
type AdminBinding = {
  id: string;
  status: string;
  error_message?: string | null;
  created_at: string;
  finished_at?: string | null;
};
type AdminPlan = {
  id: string;
  name: string;
  send_time: string;
  enabled: boolean;
  recipients: string[];
};
type AdminAccount = {
  id: string;
  name: string;
  status: string;
  created_at: string;
  bound_at?: string | null;
  last_verified_at?: string | null;
  tasks: number;
  enabled_tasks: number;
  latest_binding?: AdminBinding | null;
  plans: AdminPlan[];
};
type AdminUser = {
  id: string;
  username: string;
  email: string;
  is_admin: boolean;
  is_active: boolean;
  created_at: string;
  plan_tier: string;
  plan_period?: ProPeriod | null;
  plan_started_at?: string | null;
  plan_expires_at?: string | null;
  plan_remaining_days?: number | null;
  accounts: AdminAccount[];
};

const proPeriodText: Record<ProPeriod, string> = {
  week: '周卡',
  month: '月卡',
  quarter: '季卡',
  year: '年卡',
};
const proPeriodDays: Record<ProPeriod, number> = {
  week: 7,
  month: 30,
  quarter: 90,
  year: 365,
};

function expiredPlanName(user?: Pick<UserInfo, 'plan_period'> | null): string {
  return user?.plan_period
    ? `Pro ${proPeriodText[user.plan_period]}`
    : '体验版';
}

function utcDate(value?: string | null): Date | null {
  if (!value) return null;
  const parsed = new Date(
    /[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`,
  );
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function chinaTime(value?: string | null, dateOnly = false): string {
  const parsed = utcDate(value);
  if (!parsed) return '—';
  return new Intl.DateTimeFormat(
    'zh-CN',
    dateOnly
      ? {
          timeZone: 'Asia/Shanghai',
          year: 'numeric',
          month: 'numeric',
          day: 'numeric',
        }
      : {
          timeZone: 'Asia/Shanghai',
          year: 'numeric',
          month: 'numeric',
          day: 'numeric',
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
          hour12: false,
        },
  ).format(parsed);
}

const runStatusText: Record<string, string> = {
  pending: '等待执行',
  running: '执行中',
  retrying: '等待重试',
  recovered: '重试后成功',
  success: '成功',
  failed: '失败',
  partial: '部分完成',
  cancelled: '已取消',
  needs_login: '需要重新登录',
  needs_account: '账号未就绪',
  security_challenge: '需要安全验证',
  needs_review: '结果待确认',
  sending: '正在发送',
  submitted: '已发送',
  skipped: '已跳过',
  uncertain: '结果待确认',
};

async function api(path: string, init?: RequestInit) {
  const headers = new Headers(init?.headers);
  headers.set('Content-Type', 'application/json');
  return fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: 'include',
    headers,
  });
}

function formText(data: FormData, key: string): string {
  const value = data.get(key);
  return typeof value === 'string' ? value : '';
}

const statusText: Record<string, string> = {
  unbound: '未绑定',
  binding: '等待扫码',
  valid: '登录有效',
  invalid: '登录失效',
  challenge: '需要验证',
};
const bindingStatusText: Record<string, string> = {
  pending: '等待处理',
  running: '正在打开',
  qr_ready: '等待扫码',
  verification_required: '等待验证码',
  verification_submitted: '验证码已提交',
  complete: '绑定成功',
  error: '绑定失败',
  expired: '已过期',
  cancelled: '已取消',
};

async function responseError(response: Response): Promise<string> {
  try {
    const data = (await response.json()) as {
      detail?: string | Array<{ msg?: string }>;
    };
    if (typeof data.detail === 'string') return data.detail;
    if (Array.isArray(data.detail) && data.detail[0]?.msg)
      return String(data.detail[0].msg).replace(/^Value error, /, '');
  } catch {}
  return `请求失败（${response.status}）`;
}

function PasswordInput({
  id,
  name,
  visible,
  onToggle,
  minLength = 1,
  placeholder,
  readOnly = false,
  onChange,
}: {
  id: string;
  name: string;
  visible: boolean;
  onToggle: () => void;
  minLength?: number;
  placeholder?: string;
  readOnly?: boolean;
  onChange?: () => void;
}) {
  return (
    <div className="relative">
      <Input
        id={id}
        name={name}
        type={visible ? 'text' : 'password'}
        required
        minLength={minLength}
        placeholder={placeholder}
        readOnly={readOnly}
        onChange={onChange}
        className="h-11 rounded-xl border-[#dfe3e9] bg-white pr-11"
      />
      <button
        type="button"
        onClick={onToggle}
        className="absolute right-1 top-1 grid size-9 place-items-center rounded-lg text-[#929aa5] hover:bg-[#f2f4f7] hover:text-[#4c5561]"
        aria-label={visible ? '隐藏密码' : '显示密码'}
      >
        {visible ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
      </button>
    </div>
  );
}

function AdminAccountRuns({ accountId }: { accountId: string }) {
  const [dates, setDates] = useState<string[]>([]);
  const [date, setDate] = useState('');
  const [records, setRecords] = useState<RunInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    void api(`/api/admin/accounts/${accountId}/runs/dates`)
      .then(async (response) => {
        if (!response.ok) throw new Error(await responseError(response));
        const next = (await response.json()) as string[];
        if (!cancelled) {
          setDates(next);
          setDate(next[0] || '');
          setLoading(false);
        }
      })
      .catch((reason) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : '日期读取失败');
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [accountId]);

  useEffect(() => {
    if (!date) {
      setRecords([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError('');
    void api(
      `/api/admin/accounts/${accountId}/runs?date=${encodeURIComponent(date)}`,
    )
      .then(async (response) => {
        if (!response.ok) throw new Error(await responseError(response));
        const next = (await response.json()) as RunInfo[];
        if (!cancelled) {
          setRecords(next);
          setLoading(false);
        }
      })
      .catch((reason) => {
        if (!cancelled) {
          setError(
            reason instanceof Error ? reason.message : '运行记录读取失败',
          );
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [accountId, date]);

  const index = dates.indexOf(date);
  return (
    <div className="mt-3 overflow-hidden rounded-xl border border-black/[.06] bg-white">
      <div className="flex items-center justify-between gap-3 border-b border-black/[.055] px-3 py-3">
        <Button
          type="button"
          onClick={() => setDate(dates[index + 1])}
          disabled={loading || index < 0 || index >= dates.length - 1}
          variant="outline"
          size="sm"
          className="rounded-xl border-black/[.08] bg-white"
        >
          上一天
        </Button>
        <div className="text-center">
          <div className="text-sm font-bold">{date || '暂无记录'}</div>
          <div className="text-xs text-[#8e887f]">北京时间 · 一天一页</div>
        </div>
        <Button
          type="button"
          onClick={() => setDate(dates[index - 1])}
          disabled={loading || index <= 0}
          variant="outline"
          size="sm"
          className="rounded-xl border-black/[.08] bg-white"
        >
          下一天
        </Button>
      </div>
      {error ? (
        <div className="px-4 py-6 text-sm text-[#b25035]">{error}</div>
      ) : loading ? (
        <div className="px-4 py-6 text-sm text-[#8e887f]">
          正在读取运行记录…
        </div>
      ) : records.length ? (
        <div className="divide-y divide-black/[.06]">
          {records.map((record) => (
            <div key={record.id} className="p-4">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <div className="font-bold">{record.task_name}</div>
                  <div className="mt-1 text-xs text-[#8e887f]">
                    计划执行：{chinaTime(record.scheduled_for)}（北京时间） · 第{' '}
                    {record.attempt} 次
                  </div>
                </div>
                <Badge
                  variant="outline"
                  className={
                    ['success', 'recovered'].includes(record.status)
                      ? 'border-[#72bd91]/35 bg-[#e9f6ee] text-[#34704d]'
                      : record.status === 'failed'
                        ? 'border-[#ef604d]/25 bg-[#fff0eb] text-[#c84a34]'
                        : 'border-[#e7a742]/30 bg-[#fff8e9] text-[#94631d]'
                  }
                >
                  {runStatusText[record.status] || record.status}
                </Badge>
              </div>
              {(record.error_message ||
                record.error_code ||
                record.next_retry_at) && (
                <div className="mt-3 rounded-xl bg-[#fff3ee] p-3 text-xs leading-5 text-[#9b4b2e]">
                  {(record.error_message || record.error_code) && (
                    <div>
                      <b>失败原因：</b>
                      {record.error_message || record.error_code}
                    </div>
                  )}
                  {record.next_retry_at && (
                    <div>
                      <b>下次重试：</b>
                      {chinaTime(record.next_retry_at)}（北京时间）
                    </div>
                  )}
                </div>
              )}
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                {record.items.length ? (
                  record.items.map((item, itemIndex) => (
                    <div
                      key={`${record.id}:${itemIndex}`}
                      className="rounded-xl bg-[#f5f2ec] px-3 py-2.5 text-xs"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-semibold">
                          {item.contact_name}
                        </span>
                        <span
                          className={
                            item.status === 'submitted'
                              ? 'text-[#34704d]'
                              : 'text-[#a0632c]'
                          }
                        >
                          {runStatusText[item.status] || item.status}
                        </span>
                      </div>
                      {item.reason && (
                        <div className="mt-1 text-xs leading-5 text-[#8e887f]">
                          {item.reason}
                        </div>
                      )}
                    </div>
                  ))
                ) : (
                  <div className="text-xs text-[#8e887f]">本次没有好友明细</div>
                )}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="px-4 py-6 text-center text-sm text-[#8e887f]">
          该账号在这一天没有运行记录
        </div>
      )}
    </div>
  );
}

export default function HomePage() {
  const [active, setActive] = useState('总览');
  const [enabled, setEnabled] = useState(true);
  const [emailEnabled, setEmailEnabled] = useState(true);
  const [timeWindow, setTimeWindow] = useState('');
  const [authMode, setAuthMode] = useState<
    'login' | 'register' | 'reset' | null
  >('login');
  const [authReady, setAuthReady] = useState(false);
  const [user, setUser] = useState<UserInfo | null>(null);
  const [accounts, setAccounts] = useState<DouyinAccount[]>([]);
  const [selectedAccountId, setSelectedAccountId] = useState('');
  const [newAccountName, setNewAccountName] = useState('');
  const [creatingAccount, setCreatingAccount] = useState(false);
  const [deletingAccount, setDeletingAccount] = useState(false);
  const [accountPendingDelete, setAccountPendingDelete] =
    useState<DouyinAccount | null>(null);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [binding, setBinding] = useState<Binding | null>(null);
  const [bindingSeconds, setBindingSeconds] = useState(0);
  const [douyinVerificationCode, setDouyinVerificationCode] = useState('');
  const [verificationLoading, setVerificationLoading] = useState(false);
  const [contacts, setContacts] = useState<ContactInfo[]>([]);
  const [selectedRecipients, setSelectedRecipients] = useState<
    RecipientDraft[]
  >([]);
  const [contactFilter, setContactFilter] = useState<'all' | ConversationType>(
    'all',
  );
  const [contactsExpanded, setContactsExpanded] = useState(false);
  const [contactsDirty, setContactsDirty] = useState(false);
  const [contactsSaving, setContactsSaving] = useState(false);
  const [contactsSyncedAt, setContactsSyncedAt] = useState<string | null>(null);
  const [contactSync, setContactSync] = useState<ContactSyncInfo | null>(null);
  const [contactLoading, setContactLoading] = useState(false);
  const contactsAbortRef = useRef<AbortController | null>(null);
  const [scheduleInfo, setScheduleInfo] = useState<ScheduleInfo | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState('');
  const [accountLoading, setAccountLoading] = useState(false);
  const [prefs, setPrefs] = useState<NotificationPrefs>({
    enabled: true,
    login_expired: true,
    task_failed: true,
    security_challenge: true,
    daily_incomplete: false,
    daily_summary: false,
  });
  const [tasks, setTasks] = useState<TaskInfo[]>([]);
  const [runs, setRuns] = useState<RunInfo[]>([]);
  const [todayRunRecords, setTodayRunRecords] = useState<RunInfo[]>([]);
  const [runDates, setRunDates] = useState<string[]>([]);
  const [selectedRunDate, setSelectedRunDate] = useState('');
  const [queue, setQueue] = useState<QueueInfo>({
    position: null,
    waiting_minutes: 0,
    queued: 0,
    running: false,
  });
  const [invites, setInvites] = useState<InviteInfo[]>([]);
  const [inviteUses, setInviteUses] = useState(1);
  const [inviteDays, setInviteDays] = useState(30);
  const [generatedInvite, setGeneratedInvite] = useState('');
  const [inviteLoading, setInviteLoading] = useState(false);
  const [adminUsers, setAdminUsers] = useState<AdminUser[]>([]);
  const [adminLoading, setAdminLoading] = useState(false);
  const [upgradingUserId, setUpgradingUserId] = useState('');
  const [upgradePeriods, setUpgradePeriods] = useState<
    Record<string, ProPeriod>
  >({});
  const [expandedAdminUserId, setExpandedAdminUserId] = useState('');
  const [adminDetailMode, setAdminDetailMode] = useState<'plans' | 'runs'>(
    'plans',
  );
  const [friendsExpanded, setFriendsExpanded] = useState(false);
  const [codeSent, setCodeSent] = useState(false);
  const [localVerificationCode, setLocalVerificationCode] = useState('');
  const [authError, setAuthError] = useState('');
  const [authLoading, setAuthLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [changePasswordOpen, setChangePasswordOpen] = useState(false);
  const [changePasswordLoading, setChangePasswordLoading] = useState(false);
  const [changePasswordError, setChangePasswordError] = useState('');
  const [showCurrentPassword, setShowCurrentPassword] = useState(false);
  const [showChangedPassword, setShowChangedPassword] = useState(false);
  const [showChangedPasswordConfirm, setShowChangedPasswordConfirm] =
    useState(false);
  const [notice, setNotice] = useState('');
  const noticeTimerRef = useRef<number | null>(null);
  const selectedAccount =
    accounts.find((account) => account.id === selectedAccountId) || accounts[0];
  const accountTasks = selectedAccount
    ? tasks.filter((task) => task.account_id === selectedAccount.id)
    : [];
  const totalAssignedRecipients = tasks.reduce(
    (total, task) =>
      total + task.recipients.filter((item) => item.enabled).length,
    0,
  );
  const canCreateTask =
    tasks.length < (user?.limits.tasks_total || 0) &&
    accountTasks.length < (user?.limits.tasks_per_account || 0);
  const primaryTask =
    selectedTaskId === '__new__'
      ? undefined
      : accountTasks.find((task) => task.id === selectedTaskId) ||
        accountTasks[0];
  const accountTaskIds = new Set(accountTasks.map((task) => task.id));
  const accountRuns = runs.filter(
    (run) =>
      run.account_id === selectedAccount?.id || accountTaskIds.has(run.task_id),
  );
  const runDateIndex = runDates.indexOf(selectedRunDate);
  const olderRunDate =
    runDateIndex >= 0 ? runDates[runDateIndex + 1] : undefined;
  const newerRunDate =
    runDateIndex > 0 ? runDates[runDateIndex - 1] : undefined;
  const filteredContacts = contacts.filter(
    (contact) =>
      contactFilter === 'all' || contact.conversation_type === contactFilter,
  );
  const visibleFilteredContacts = contactsExpanded
    ? filteredContacts
    : filteredContacts.slice(0, 12);
  const conversationLabel: Record<ConversationType, string> = {
    friend: '好友',
    group: '群聊',
    unknown: '待识别',
  };
  const assignedTaskByContact = new Map<string, TaskInfo>();
  accountTasks.forEach((task) =>
    task.recipients.forEach((recipient) => {
      if (recipient.contact_id)
        assignedTaskByContact.set(recipient.contact_id, task);
    }),
  );
  const showNotice = (text: string) => {
    setNotice(text);
    if (noticeTimerRef.current) window.clearTimeout(noticeTimerRef.current);
    noticeTimerRef.current = window.setTimeout(() => {
      setNotice('');
      noticeTimerRef.current = null;
    }, 2200);
  };

  const loadAccounts = async () => {
    const response = await api('/api/accounts');
    if (response.ok) {
      const next = (await response.json()) as DouyinAccount[];
      setAccounts(next);
      setSelectedAccountId((current) =>
        next.some((account) => account.id === current)
          ? current
          : next[0]?.id || '',
      );
    }
  };

  const loadPreferences = async () => {
    const response = await api('/api/settings/notifications');
    if (response.ok) {
      const value = (await response.json()) as NotificationPrefs;
      setPrefs(value);
      setEmailEnabled(value.enabled);
    }
  };

  const loadOperations = async () => {
    const today = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).format(new Date());
    const [taskResponse, dateResponse, queueResponse, todayResponse] =
      await Promise.all([
        api('/api/tasks'),
        api('/api/runs/dates'),
        api('/api/queue'),
        api(`/api/runs?date=${today}`),
      ]);
    if (taskResponse.ok) {
      const nextTasks = (await taskResponse.json()) as TaskInfo[];
      setTasks(nextTasks);
    }
    if (dateResponse.ok) {
      const dates = (await dateResponse.json()) as string[];
      setRunDates(dates);
      const targetDate = dates.includes(selectedRunDate)
        ? selectedRunDate
        : dates[0] || '';
      setSelectedRunDate(targetDate);
      if (targetDate) {
        const runResponse = await api(
          `/api/runs?date=${encodeURIComponent(targetDate)}`,
        );
        if (runResponse.ok) setRuns(await runResponse.json());
      } else {
        setRuns([]);
      }
    }
    if (queueResponse.ok) setQueue(await queueResponse.json());
    if (todayResponse.ok) setTodayRunRecords(await todayResponse.json());
  };

  const loadRunsForDate = async (date: string) => {
    if (!date) {
      setRuns([]);
      return;
    }
    const response = await api(`/api/runs?date=${encodeURIComponent(date)}`);
    if (response.ok) setRuns(await response.json());
  };

  const loadSavedContacts = async (accountId: string) => {
    contactsAbortRef.current?.abort();
    const controller = new AbortController();
    contactsAbortRef.current = controller;
    setContactLoading(true);
    try {
      const response = await api(`/api/accounts/${accountId}/contacts`, {
        signal: controller.signal,
      });
      if (response.ok && !controller.signal.aborted) {
        const data = (await response.json()) as {
          contacts: ContactInfo[];
          synced_at?: string | null;
        };
        if (controller.signal.aborted) return;
        setContacts(data.contacts);
        setContactsSyncedAt(data.synced_at || null);
        setContactsDirty(false);
      }
    } catch (error) {
      if (!(error instanceof DOMException && error.name === 'AbortError'))
        showNotice('好友列表读取失败，请重试');
    } finally {
      if (contactsAbortRef.current === controller) setContactLoading(false);
    }
  };

  const loadInvites = async () => {
    const response = await api('/api/admin/invites');
    if (response.ok) setInvites(await response.json());
  };

  const loadAdminUsers = async (silent = false) => {
    if (!silent) setAdminLoading(true);
    try {
      const response = await api('/api/admin/users');
      if (!response.ok) throw new Error(await responseError(response));
      setAdminUsers(await response.json());
    } catch (error) {
      if (!silent)
        showNotice(error instanceof Error ? error.message : '用户列表读取失败');
    } finally {
      if (!silent) setAdminLoading(false);
    }
  };

  const clearStuckBinding = async (bindingId: string) => {
    const response = await api(`/api/admin/bindings/${bindingId}/cancel`, {
      method: 'POST',
    });
    if (!response.ok) {
      showNotice(await responseError(response));
      return;
    }
    await loadAdminUsers();
    showNotice('卡住的绑定会话已清理，用户现在可以重新绑定');
  };

  const upgradeUserToPro = async (managedUser: AdminUser) => {
    const period = upgradePeriods[managedUser.id] || 'year';
    if (
      !window.confirm(
        `确认将 ${managedUser.username} 开通为 Pro ${proPeriodText[period]}（${proPeriodDays[period]} 天）吗？`,
      )
    )
      return;
    setUpgradingUserId(managedUser.id);
    try {
      const response = await api(
        `/api/admin/users/${managedUser.id}/upgrade-pro`,
        { method: 'POST', body: JSON.stringify({ period }) },
      );
      if (!response.ok) throw new Error(await responseError(response));
      await loadAdminUsers(true);
      showNotice(`${managedUser.username} 已开通 Pro ${proPeriodText[period]}`);
    } catch (error) {
      showNotice(error instanceof Error ? error.message : '升级 Pro 失败');
    } finally {
      setUpgradingUserId('');
    }
  };

  const createInvite = async () => {
    setGeneratedInvite('');
    setInviteLoading(true);
    try {
      const response = await api('/api/admin/invites', {
        method: 'POST',
        body: JSON.stringify({
          max_uses: inviteUses,
          expires_in_days: inviteDays,
        }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      const result = (await response.json()) as { code: string };
      setGeneratedInvite(result.code);
      await loadInvites();
      showNotice('新的邀请码已生成，请确认页面新代码后再复制');
    } catch (error) {
      showNotice(error instanceof Error ? error.message : '邀请码生成失败');
    } finally {
      setInviteLoading(false);
    }
  };

  const setInviteActive = async (invite: InviteInfo, active: boolean) => {
    const response = await api(`/api/admin/invites/${invite.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ active }),
    });
    if (!response.ok) {
      showNotice(await responseError(response));
      return;
    }
    await loadInvites();
    showNotice(active ? '邀请码已重新启用' : '邀请码已停用');
  };

  const removeInvite = async (invite: InviteInfo) => {
    if (!window.confirm('确定删除这个未使用的邀请码吗？删除后无法恢复。'))
      return;
    const response = await api(`/api/admin/invites/${invite.id}`, {
      method: 'DELETE',
    });
    if (!response.ok) {
      showNotice(await responseError(response));
      return;
    }
    await loadInvites();
    showNotice('邀请码已删除');
  };

  const copyInvite = async () => {
    if (!generatedInvite) return;
    try {
      await navigator.clipboard.writeText(generatedInvite);
      showNotice('邀请码已复制，可以发给朋友了');
    } catch {
      showNotice('复制失败，请长按邀请码手动复制');
    }
  };

  const savePreferences = async (next: NotificationPrefs) => {
    const previous = prefs;
    setPrefs(next);
    setEmailEnabled(next.enabled);
    const response = await api('/api/settings/notifications', {
      method: 'PUT',
      body: JSON.stringify(next),
    });
    if (!response.ok) {
      setPrefs(previous);
      setEmailEnabled(previous.enabled);
    }
    showNotice(response.ok ? '通知偏好已保存' : await responseError(response));
  };

  const sendTestEmail = async () => {
    const response = await api('/api/settings/notifications/test', {
      method: 'POST',
    });
    showNotice(
      response.ok ? '测试邮件已进入发送队列' : await responseError(response),
    );
  };

  useEffect(() => {
    api('/api/me')
      .then(async (response) => {
        if (response.ok) {
          setUser(await response.json());
          setAuthMode(null);
          await loadAccounts();
          await loadPreferences();
          await loadOperations();
        } else {
          setAuthMode('login');
        }
      })
      .catch(() => setAuthMode('login'))
      .finally(() => setAuthReady(true));
  }, []);

  useEffect(() => {
    if (active === '我的管理' && user?.is_admin)
      void Promise.all([loadInvites(), loadAdminUsers()]);
  }, [active, user?.is_admin]);

  useEffect(() => {
    if (!user) return;
    let refreshing = false;
    const refreshAutomatically = async () => {
      if (refreshing) return;
      refreshing = true;
      try {
        const requests: Promise<unknown>[] = [loadOperations()];
        if (active === '总览') requests.push(loadAccounts());
        if (active === '我的管理' && user.is_admin)
          requests.push(loadInvites(), loadAdminUsers(true));
        const meResponse = await api('/api/me');
        if (meResponse.ok) {
          setUser(await meResponse.json());
        } else if (meResponse.status === 401) {
          setUser(null);
          setAuthMode('login');
          setRuns([]);
          setTodayRunRecords([]);
          return;
        }
        await Promise.all(requests);
      } finally {
        refreshing = false;
      }
    };
    const timer = window.setInterval(
      () => void refreshAutomatically(),
      active === '我的管理' ? 60000 : 15000,
    );
    return () => window.clearInterval(timer);
  }, [active, user?.id, user?.is_admin, selectedRunDate]);

  useEffect(() => {
    setBinding(null);
    setContacts([]);
    setSelectedRecipients([]);
    setContactsSyncedAt(null);
    setContactsDirty(false);
    setContactSync(null);
    if (selectedAccountId) void loadSavedContacts(selectedAccountId);
    setSelectedTaskId('');
    return () => contactsAbortRef.current?.abort();
  }, [selectedAccountId]);

  useEffect(() => {
    if (!selectedAccountId) return;
    setSelectedTaskId((current) => {
      if (
        current === '__new__' ||
        accountTasks.some((task) => task.id === current)
      )
        return current;
      return accountTasks[0]?.id || '__new__';
    });
  }, [selectedAccountId, tasks]);

  useEffect(() => {
    let cancelled = false;
    if (primaryTask) {
      setTimeWindow(primaryTask.send_time);
      setEnabled(primaryTask.enabled);
    } else {
      setTimeWindow('');
      setEnabled(true);
      void api('/api/schedule/recommendation').then(async (response) => {
        if (!cancelled && response.ok) {
          const recommendation = (await response.json()) as ScheduleInfo;
          setTimeWindow(recommendation.send_time);
          setScheduleInfo(recommendation);
        }
      });
    }
    return () => {
      cancelled = true;
    };
  }, [primaryTask?.id, user?.id]);

  useEffect(() => {
    if (primaryTask) {
      setSelectedRecipients(
        primaryTask.recipients
          .filter((item) => item.enabled)
          .map((item) => ({
            contact_id:
              item.contact_id ||
              contacts.find(
                (contact) => contact.display_name === item.contact_name,
              )?.id ||
              '',
            contact_name: item.contact_name,
            conversation_type: item.conversation_type,
            message: item.message,
          })),
      );
    } else {
      setSelectedRecipients([]);
    }
    setContactsDirty(false);
  }, [primaryTask, contacts]);

  useEffect(() => {
    if (user && selectedRunDate) void loadRunsForDate(selectedRunDate);
  }, [selectedRunDate, user?.id]);

  useEffect(() => {
    if (!/^\d{2}:\d{2}$/.test(timeWindow) || !user) return;
    const timer = window.setTimeout(async () => {
      const exclude = primaryTask
        ? `&exclude_task_id=${encodeURIComponent(primaryTask.id)}`
        : '';
      const response = await api(
        `/api/schedule/availability?send_time=${encodeURIComponent(timeWindow)}${exclude}`,
      );
      if (response.ok) setScheduleInfo(await response.json());
    }, 350);
    return () => window.clearTimeout(timer);
  }, [timeWindow, primaryTask?.id, user?.id]);

  useEffect(() => {
    if (
      !contactSync ||
      !selectedAccount ||
      ['complete', 'error'].includes(contactSync.status)
    )
      return;
    const timer = window.setInterval(async () => {
      const response = await api(
        `/api/accounts/${selectedAccount.id}/contacts/sync/${contactSync.id}`,
      );
      if (!response.ok) return;
      const next = (await response.json()) as ContactSyncInfo;
      setContactSync(next);
      if (next.status === 'complete') {
        await loadSavedContacts(selectedAccount.id);
        setContactsSyncedAt(next.finished_at || new Date().toISOString());
        showNotice(`已读取并保存 ${next.contacts.length} 个会话`);
      } else if (next.status === 'error') {
        setContactLoading(false);
        showNotice(next.error_message || '好友读取失败');
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [contactSync?.id, contactSync?.status, selectedAccount?.id]);

  const syncContacts = async () => {
    if (!selectedAccount || selectedAccount.status !== 'valid') {
      showNotice('请先扫码绑定当前抖音账号');
      setActive('账号绑定');
      return;
    }
    setContactLoading(true);
    const response = await api(
      `/api/accounts/${selectedAccount.id}/contacts/sync`,
      { method: 'POST' },
    );
    if (!response.ok) {
      setContactLoading(false);
      showNotice(await responseError(response));
      return;
    }
    setContactSync({
      ...(await response.json()),
      contacts: [],
    } as ContactSyncInfo);
    showNotice('已加入读取队列，请稍候');
  };

  const toggleContact = (contact: ContactInfo) => {
    setSelectedRecipients((current) => {
      if (current.some((item) => item.contact_id === contact.id))
        return current.filter((item) => item.contact_id !== contact.id);
      const assignedTask = assignedTaskByContact.get(contact.id);
      if (assignedTask && assignedTask.id !== primaryTask?.id) {
        showNotice(
          `“${contact.display_name}”已在${assignedTask.name}中，请先从原计划移除`,
        );
        return current;
      }
      if (current.length >= 5) {
        showNotice('每个计划最多选择 5 个会话');
        return current;
      }
      const recipientsOutsideCurrentPlan =
        totalAssignedRecipients -
        (primaryTask?.recipients.filter((item) => item.enabled).length || 0);
      if (
        recipientsOutsideCurrentPlan + current.length >=
        (user?.limits.recipients_total || 0)
      ) {
        showNotice(
          `所有抖音账号的计划合计最多安排 ${user?.limits.recipients_total || 0} 人`,
        );
        return current;
      }
      return [
        ...current,
        {
          contact_id: contact.id,
          contact_name: contact.display_name,
          conversation_type: contact.conversation_type,
          message: contact.message || '今日火花',
        },
      ];
    });
    setContactsDirty(true);
  };

  const updateRecipientMessage = (recipientIndex: number, message: string) => {
    setSelectedRecipients((current) =>
      current.map((item, index) =>
        index === recipientIndex ? { ...item, message } : item,
      ),
    );
    setContactsDirty(true);
  };

  const saveContactSelection = async () => {
    if (!selectedAccount) return;
    if (!primaryTask) {
      setActive('执行计划');
      showNotice('已暂存所选好友，请设置时间并创建计划');
      return;
    }
    if (!selectedRecipients.length) {
      showNotice('每个计划至少需要 1 位好友');
      return;
    }
    if (selectedRecipients.some((item) => !item.message.trim())) {
      showNotice('每个已选会话都需要填写消息');
      return;
    }
    setContactsSaving(true);
    try {
      const response = await api(`/api/tasks/${primaryTask.id}`, {
        method: 'PUT',
        body: JSON.stringify({
          account_id: selectedAccount.id,
          name: primaryTask.name,
          send_time: primaryTask.send_time,
          enabled: primaryTask.enabled,
          recipients: selectedRecipients,
        }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      setContactsDirty(false);
      await loadOperations();
      showNotice(`${primaryTask.name}的好友和消息已保存`);
    } catch (error) {
      showNotice(error instanceof Error ? error.message : '好友设置保存失败');
    } finally {
      setContactsSaving(false);
    }
  };

  const updateContactType = async (
    contact: ContactInfo,
    conversationType: ConversationType,
  ) => {
    if (!selectedAccount || contact.conversation_type === conversationType)
      return;
    const response = await api(
      `/api/accounts/${selectedAccount.id}/contacts/${contact.id}`,
      {
        method: 'PATCH',
        body: JSON.stringify({ conversation_type: conversationType }),
      },
    );
    if (!response.ok) {
      showNotice(await responseError(response));
      return;
    }
    setContacts((current) =>
      current.map((item) =>
        item.id === contact.id
          ? { ...item, conversation_type: conversationType, type_locked: true }
          : item,
      ),
    );
    setSelectedRecipients((current) =>
      current.map((item) =>
        item.contact_id === contact.id
          ? { ...item, conversation_type: conversationType }
          : item,
      ),
    );
    await loadOperations();
    showNotice(
      `已将“${contact.display_name}”标记为${conversationLabel[conversationType]}`,
    );
  };

  useEffect(() => {
    if (
      !binding ||
      !selectedAccount ||
      ['complete', 'error', 'expired'].includes(binding.status)
    )
      return;
    const timer = window.setInterval(async () => {
      const response = await api(
        `/api/accounts/${selectedAccount.id}/binding/${binding.id}`,
      );
      if (!response.ok) return;
      const next = (await response.json()) as Binding;
      setBinding(next);
      if (next.status === 'complete') {
        window.clearInterval(timer);
        await loadAccounts();
        showNotice('抖音账号绑定成功');
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [binding?.id, binding?.status, selectedAccount?.id]);

  useEffect(() => {
    setBindingSeconds(binding?.remaining_seconds || 0);
    if (!binding || ['complete', 'error', 'expired'].includes(binding.status))
      return;
    const timer = window.setInterval(
      () => setBindingSeconds((current) => Math.max(0, current - 1)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [binding?.id, binding?.remaining_seconds, binding?.status]);

  const addAccount = async () => {
    const name = newAccountName.trim();
    if (!name) {
      showNotice('请先填写账号备注名');
      return;
    }
    setCreatingAccount(true);
    try {
      const response = await api('/api/accounts', {
        method: 'POST',
        body: JSON.stringify({ name }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      const account = (await response.json()) as DouyinAccount;
      setAccounts((current) => [...current, account]);
      setSelectedAccountId(account.id);
      setNewAccountName('');
      setBinding(null);
      showNotice('抖音账号已添加，现在可以扫码绑定');
    } catch (error) {
      showNotice(error instanceof Error ? error.message : '添加账号失败');
    } finally {
      setCreatingAccount(false);
    }
  };

  const removeAccount = async () => {
    if (!accountPendingDelete) return;
    setDeletingAccount(true);
    try {
      const response = await api(`/api/accounts/${accountPendingDelete.id}`, {
        method: 'DELETE',
      });
      if (!response.ok) throw new Error(await responseError(response));
      const remaining = accounts.filter(
        (account) => account.id !== accountPendingDelete.id,
      );
      setAccounts(remaining);
      setSelectedAccountId(remaining[0]?.id || '');
      setSelectedTaskId('');
      setBinding(null);
      setContacts([]);
      setSelectedRecipients([]);
      setContactSync(null);
      setAccountPendingDelete(null);
      await loadOperations();
      showNotice('抖音账号及其关联数据已删除');
    } catch (error) {
      showNotice(error instanceof Error ? error.message : '删除账号失败');
    } finally {
      setDeletingAccount(false);
    }
  };

  const startBinding = async () => {
    setAccountLoading(true);
    try {
      let account = selectedAccount;
      if (!account) {
        const created = await api('/api/accounts', {
          method: 'POST',
          body: JSON.stringify({ name: '我的抖音账号' }),
        });
        if (!created.ok) throw new Error(await responseError(created));
        account = await created.json();
        setAccounts((current) => [...current, account]);
        setSelectedAccountId(account.id);
      }
      const response = await api(`/api/accounts/${account.id}/binding`, {
        method: 'POST',
      });
      if (!response.ok) throw new Error(await responseError(response));
      setDouyinVerificationCode('');
      setBinding(await response.json());
    } catch (error) {
      showNotice(error instanceof Error ? error.message : '无法开始扫码');
    } finally {
      setAccountLoading(false);
    }
  };

  const restartBinding = async () => {
    if (!selectedAccount || !binding) return;
    setAccountLoading(true);
    try {
      const cancelled = await api(
        `/api/accounts/${selectedAccount.id}/binding/${binding.id}/cancel`,
        { method: 'POST' },
      );
      if (!cancelled.ok && cancelled.status !== 409)
        throw new Error(await responseError(cancelled));
      const response = await api(
        `/api/accounts/${selectedAccount.id}/binding`,
        { method: 'POST' },
      );
      if (!response.ok) throw new Error(await responseError(response));
      setDouyinVerificationCode('');
      setBinding(await response.json());
      showNotice('旧二维码已作废，正在打开新的抖音登录页面');
    } catch (error) {
      showNotice(error instanceof Error ? error.message : '无法重新生成二维码');
    } finally {
      setAccountLoading(false);
    }
  };

  const cancelBinding = async () => {
    if (!selectedAccount || !binding) return;
    setAccountLoading(true);
    try {
      const response = await api(
        `/api/accounts/${selectedAccount.id}/binding/${binding.id}/cancel`,
        { method: 'POST' },
      );
      if (!response.ok) throw new Error(await responseError(response));
      setBinding((current) =>
        current
          ? {
              ...current,
              status: 'expired',
              qr_data_url: null,
              error_message: '绑定已取消',
              remaining_seconds: 0,
            }
          : current,
      );
      await loadAccounts();
      showNotice('本次绑定已取消，二维码已作废');
    } catch (error) {
      showNotice(error instanceof Error ? error.message : '无法取消绑定');
    } finally {
      setAccountLoading(false);
    }
  };

  const submitDouyinVerification = async () => {
    if (!selectedAccount || !binding) return;
    if (!/^\d{4,6}$/.test(douyinVerificationCode)) {
      showNotice('请输入 4—6 位数字验证码');
      return;
    }
    setVerificationLoading(true);
    try {
      const response = await api(
        `/api/accounts/${selectedAccount.id}/binding/${binding.id}/verification`,
        {
          method: 'POST',
          body: JSON.stringify({ code: douyinVerificationCode }),
        },
      );
      if (!response.ok) throw new Error(await responseError(response));
      setBinding((current) =>
        current
          ? {
              ...current,
              status: 'verification_submitted',
              error_message: '验证码已提交，正在等待抖音确认',
            }
          : current,
      );
      setDouyinVerificationCode('');
      showNotice('验证码已提交，请等待抖音确认登录');
    } catch (error) {
      showNotice(error instanceof Error ? error.message : '验证码提交失败');
    } finally {
      setVerificationLoading(false);
    }
  };

  const logout = async () => {
    await api('/api/auth/logout', { method: 'POST' });
    setUser(null);
    setAccounts([]);
    setSelectedAccountId('');
    setShowPassword(false);
    setShowConfirmPassword(false);
    setAuthMode('login');
  };

  const createTask = async () => {
    if (user?.plan_tier === 'expired') {
      showNotice(`${expiredPlanName(user)}已到期，当前不能保存自动计划`);
      return;
    }
    if (!selectedAccount || selectedAccount.status !== 'valid') {
      showNotice('请先完成当前抖音账号的扫码绑定');
      setActive('账号绑定');
      return;
    }
    if (!selectedRecipients.length || selectedRecipients.length > 5) {
      showNotice('请先选择并保存 1—5 个会话');
      setActive('火花好友');
      return;
    }
    if (selectedRecipients.some((item) => !item.message.trim())) {
      showNotice('每个已选会话都需要填写消息');
      return;
    }
    if (scheduleInfo && !scheduleInfo.available) {
      showNotice('这个时间附近的服务器队列已满，请换一个时间');
      return;
    }
    const response = await api(
      primaryTask ? `/api/tasks/${primaryTask.id}` : '/api/tasks',
      {
        method: primaryTask ? 'PUT' : 'POST',
        body: JSON.stringify({
          account_id: selectedAccount.id,
          name: primaryTask?.name || `每日续火 ${accountTasks.length + 1}`,
          send_time: timeWindow,
          enabled,
          recipients: selectedRecipients,
        }),
      },
    );
    if (!response.ok) {
      showNotice(await responseError(response));
      return;
    }
    const saved = (await response.json()) as TaskInfo;
    setSelectedTaskId(saved.id);
    await loadOperations();
    showNotice(primaryTask ? '执行计划已更新' : '执行计划已创建');
  };

  const deleteTask = async () => {
    if (
      !primaryTask ||
      !window.confirm(
        '确定删除这个执行计划吗？历史运行记录和已保存的联系人都会保留。',
      )
    )
      return;
    const response = await api(`/api/tasks/${primaryTask.id}`, {
      method: 'DELETE',
    });
    if (!response.ok) {
      showNotice(await responseError(response));
      return;
    }
    setSelectedTaskId('__new__');
    await loadOperations();
    showNotice('执行计划已删除，历史运行记录和好友设置仍然保留');
  };

  const toggleTask = async (value: boolean, targetTask = primaryTask) => {
    if (value && user?.plan_tier === 'expired') {
      showNotice(`${expiredPlanName(user)}已到期，当前不能启用自动计划`);
      return;
    }
    if (!targetTask) {
      setEnabled(value);
      showNotice('请先创建执行计划');
      return;
    }
    const response = await api(`/api/tasks/${targetTask.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ enabled: value }),
    });
    if (!response.ok) {
      showNotice(await responseError(response));
      return;
    }
    if (targetTask.id === primaryTask?.id) setEnabled(value);
    await loadOperations();
    showNotice(value ? '自动值守已开启' : '自动值守已暂停');
  };

  const toggleAllAccountTasks = async (value: boolean) => {
    if (value && user?.plan_tier === 'expired') {
      showNotice(`${expiredPlanName(user)}已到期，当前不能启用自动计划`);
      return;
    }
    if (!accountTasks.length) {
      showNotice('请先创建执行计划');
      return;
    }
    const responses = await Promise.all(
      accountTasks.map((task) =>
        api(`/api/tasks/${task.id}`, {
          method: 'PATCH',
          body: JSON.stringify({ enabled: value }),
        }),
      ),
    );
    await loadOperations();
    const failedResponse = responses.find((response) => !response.ok);
    if (failedResponse) {
      showNotice(await responseError(failedResponse));
      return;
    }
    showNotice(value ? '当前账号的全部计划已开启' : '当前账号的全部计划已暂停');
  };

  const handleChangePassword = async (
    event: SyntheticEvent<HTMLFormElement>,
  ) => {
    event.preventDefault();
    setChangePasswordError('');
    const data = new FormData(event.currentTarget);
    const currentPassword = formText(data, 'currentPassword');
    const password = formText(data, 'changedPassword');
    const confirmPassword = formText(data, 'changedPasswordConfirm');
    if (
      password.length < 8 ||
      !/[A-Z]/.test(password) ||
      !/[a-z]/.test(password) ||
      !/\d/.test(password)
    ) {
      setChangePasswordError(
        '新密码至少 8 位，并且需要同时包含大写字母、小写字母和数字',
      );
      return;
    }
    if (password !== confirmPassword) {
      setChangePasswordError('两次输入的新密码不一致');
      return;
    }
    setChangePasswordLoading(true);
    try {
      const response = await api('/api/auth/password/change', {
        method: 'POST',
        body: JSON.stringify({
          current_password: currentPassword,
          password,
          confirm_password: confirmPassword,
        }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      setChangePasswordOpen(false);
      setShowCurrentPassword(false);
      setShowChangedPassword(false);
      setShowChangedPasswordConfirm(false);
      showNotice('密码已修改，其他设备的登录已退出');
    } catch (error) {
      setChangePasswordError(
        error instanceof Error ? error.message : '修改密码失败',
      );
    } finally {
      setChangePasswordLoading(false);
    }
  };

  const handleAuthSubmit = async (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    setAuthError('');
    const data = new FormData(event.currentTarget);
    const username = formText(data, 'username');
    const password = formText(data, 'password');
    const email = formText(data, 'email');
    const confirmPassword = formText(data, 'confirmPassword');
    if (authMode === 'register' && !/^[A-Za-z0-9_-]{3,32}$/.test(username)) {
      setAuthError('用户名须为 3–32 位字母、数字、下划线或短横线');
      return;
    }
    if (authMode === 'login' && !username.trim()) {
      setAuthError('请输入用户名或邮箱');
      return;
    }
    if (
      (authMode === 'register' || (authMode === 'reset' && codeSent)) &&
      (password.length < 8 ||
        !/[A-Z]/.test(password) ||
        !/[a-z]/.test(password) ||
        !/\d/.test(password))
    ) {
      setAuthError('密码至少 8 位，并且需要同时包含大写字母、小写字母和数字');
      return;
    }
    if (
      (authMode === 'register' || (authMode === 'reset' && codeSent)) &&
      password !== confirmPassword
    ) {
      setAuthError('两次输入的密码不一致');
      return;
    }
    setAuthLoading(true);
    try {
      if (authMode === 'reset') {
        if (!codeSent) {
          const response = await api('/api/auth/password/reset/start', {
            method: 'POST',
            body: JSON.stringify({ email }),
          });
          if (!response.ok) throw new Error(await responseError(response));
          const result = (await response.json()) as {
            verification_code?: string;
          };
          setLocalVerificationCode(result.verification_code || '');
          setCodeSent(true);
          showNotice(
            result.verification_code
              ? '当前部署未启用邮件验证，请使用页面显示的本地验证码'
              : '如果邮箱已注册，验证码会发送到该邮箱',
          );
          return;
        }
        const response = await api('/api/auth/password/reset/verify', {
          method: 'POST',
          body: JSON.stringify({
            email,
            code: formText(data, 'code'),
            password,
            confirm_password: confirmPassword,
          }),
        });
        if (!response.ok) throw new Error(await responseError(response));
        setAuthMode('login');
        setCodeSent(false);
        setShowPassword(false);
        setShowConfirmPassword(false);
        showNotice('密码已重置，请使用新密码登录');
        return;
      }
      if (authMode === 'register' && !codeSent) {
        const response = await fetch(`${API_BASE}/api/auth/register/start`, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            username,
            email,
            password,
            confirm_password: confirmPassword,
            invite_code: formText(data, 'invite'),
          }),
        });
        if (!response.ok) throw new Error(await responseError(response));
        const result = (await response.json()) as {
          verification_code?: string;
        };
        setLocalVerificationCode(result.verification_code || '');
        setCodeSent(true);
        showNotice(
          result.verification_code
            ? '当前部署未启用邮件验证，请使用页面显示的本地验证码'
            : '验证码已发送，请检查邮箱',
        );
        return;
      }
      const path =
        authMode === 'register'
          ? '/api/auth/register/verify'
          : '/api/auth/login';
      const body =
        authMode === 'register'
          ? { email, code: formText(data, 'code') }
          : { username, password };
      const response = await api(path, {
        method: 'POST',
        body: JSON.stringify(body),
      });
      if (!response.ok) throw new Error(await responseError(response));
      const result = (await response.json()) as { user: UserInfo };
      setUser(result.user);
      setAuthMode(null);
      await loadAccounts();
      await loadPreferences();
      await loadOperations();
      showNotice(
        authMode === 'register' ? '邮箱验证成功，账号已创建' : '登录成功',
      );
    } catch (error) {
      setAuthError(
        error instanceof Error ? error.message : '请求失败，请稍后重试',
      );
    } finally {
      setAuthLoading(false);
    }
  };

  if (!authReady) {
    return (
      <main className="grid min-h-screen place-items-center bg-[#eef1f6] text-sm text-[#7b8491]">
        正在安全连接…
      </main>
    );
  }

  if (authMode) {
    const isRegister = authMode === 'register';
    const isReset = authMode === 'reset';
    return (
      <main className="min-h-screen bg-[#eef1f6] px-5 py-10 text-[#1f2937] sm:py-12">
        {process.env.NODE_ENV === 'development' && (
          <button
            onClick={() => setAuthMode(null)}
            className="fixed right-5 top-5 rounded-full border border-black/[.06] bg-white/80 px-4 py-2 text-[11px] text-[#7b8491] shadow-sm backdrop-blur hover:text-[#242a32]"
          >
            进入后台预览
          </button>
        )}
        <section className="mx-auto w-full max-w-[500px] rounded-[24px] bg-white px-6 py-8 shadow-[0_18px_60px_rgba(37,51,76,.09)] sm:px-10 sm:py-10">
          <div className="flex items-center gap-3">
            <div className="grid size-10 place-items-center rounded-xl bg-[#ff6854] text-white shadow-[0_8px_20px_rgba(255,104,84,.25)]">
              <Flame className="size-5 fill-white" />
            </div>
            <div>
              <div className="font-bold tracking-tight">火花值守</div>
              <div className="text-[10px] text-[#8b94a1]">
                让每天的问候准时抵达
              </div>
            </div>
          </div>
          <div className="mt-10">
            <h1 className="text-[28px] font-bold tracking-[-.04em]">
              {isReset ? '重置密码' : isRegister ? '创建账号' : '欢迎回来'}
            </h1>
            <p className="mt-2 text-sm text-[#7b8491]">
              {isReset
                ? codeSent
                  ? '输入邮件验证码并设置一个新密码。'
                  : '先验证你的注册邮箱。'
                : isRegister
                  ? '输入管理员提供的邀请码即可加入。'
                  : '使用你的账号登录火花值守。'}
            </p>
          </div>
          <form className="mt-7 space-y-5" onSubmit={handleAuthSubmit}>
            {!isReset && (
              <div className="space-y-2">
                <Label htmlFor="username" className="text-xs font-bold">
                  {isRegister ? '用户名' : '用户名或邮箱'}
                </Label>
                <Input
                  id="username"
                  name="username"
                  required
                  minLength={isRegister ? 3 : 1}
                  maxLength={320}
                  pattern={isRegister ? '[A-Za-z0-9_-]{3,32}' : undefined}
                  readOnly={isRegister && codeSent}
                  onChange={() => setAuthError('')}
                  className="h-11 rounded-xl border-[#dfe3e9] bg-white"
                />
                {isRegister && (
                  <p className="text-[10px] text-[#87909d]">
                    3–32 位字母、数字、下划线或短横线
                  </p>
                )}
              </div>
            )}
            {(isRegister || isReset) && (
              <div className="space-y-2">
                <Label htmlFor="email" className="text-xs font-bold">
                  注册邮箱
                </Label>
                <Input
                  id="email"
                  name="email"
                  type="email"
                  required
                  readOnly={codeSent}
                  onChange={() => setAuthError('')}
                  className="h-11 rounded-xl border-[#dfe3e9] bg-white read-only:bg-[#f6f7f9]"
                />
                <p className="text-[10px] text-[#87909d]">
                  {isReset
                    ? '无论邮箱是否注册，页面都会显示相同提示'
                    : '验证码和异常提醒都会发送到这个邮箱'}
                </p>
              </div>
            )}
            {(!isReset || codeSent) && (
              <div className="space-y-2">
                <div className="flex justify-between">
                  <Label htmlFor="password" className="text-xs font-bold">
                    {isReset ? '新密码' : '密码'}
                  </Label>
                  {authMode === 'login' && (
                    <button
                      type="button"
                      onClick={() => {
                        setAuthMode('reset');
                        setCodeSent(false);
                        setAuthError('');
                        setShowPassword(false);
                      }}
                      className="text-[11px] font-semibold text-[#ef604d]"
                    >
                      忘记密码？
                    </button>
                  )}
                </div>
                <PasswordInput
                  id="password"
                  name="password"
                  visible={showPassword}
                  onToggle={() => setShowPassword((value) => !value)}
                  minLength={isRegister || isReset ? 8 : 1}
                  readOnly={isRegister && codeSent}
                  onChange={() => setAuthError('')}
                />
                {(isRegister || isReset) && (
                  <div className="flex flex-wrap gap-2">
                    <span className="rounded-full bg-[#f3f5f8] px-2.5 py-1 text-[10px] text-[#7d8693]">
                      至少 8 位
                    </span>
                    <span className="rounded-full bg-[#f3f5f8] px-2.5 py-1 text-[10px] text-[#7d8693]">
                      含大写字母
                    </span>
                    <span className="rounded-full bg-[#f3f5f8] px-2.5 py-1 text-[10px] text-[#7d8693]">
                      含小写字母
                    </span>
                    <span className="rounded-full bg-[#f3f5f8] px-2.5 py-1 text-[10px] text-[#7d8693]">
                      包含数字
                    </span>
                  </div>
                )}
              </div>
            )}
            {(isRegister || (isReset && codeSent)) && (
              <div className="space-y-2">
                <Label htmlFor="confirm-password" className="text-xs font-bold">
                  确认{isReset ? '新' : ''}密码
                </Label>
                <PasswordInput
                  id="confirm-password"
                  name="confirmPassword"
                  visible={showConfirmPassword}
                  onToggle={() => setShowConfirmPassword((value) => !value)}
                  minLength={8}
                  readOnly={isRegister && codeSent}
                  onChange={() => setAuthError('')}
                />
              </div>
            )}
            {isRegister && (
              <div className="space-y-2">
                <Label htmlFor="invite" className="text-xs font-bold">
                  邀请码
                </Label>
                <div className="relative">
                  <KeyRound className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[#a0a7b1]" />
                  <Input
                    id="invite"
                    name="invite"
                    required
                    onChange={() => setAuthError('')}
                    className="h-11 rounded-xl border-[#dfe3e9] bg-white pl-9 uppercase"
                  />
                </div>
              </div>
            )}
            {(isRegister || isReset) && codeSent && (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label htmlFor="code" className="text-xs font-bold">
                    {localVerificationCode ? '本地验证码' : '邮箱验证码'}
                  </Label>
                  <button
                    type="button"
                    onClick={() => {
                      setCodeSent(false);
                      setLocalVerificationCode('');
                      setAuthError('');
                    }}
                    className="text-[11px] font-semibold text-[#ef604d]"
                  >
                    更换邮箱
                  </button>
                </div>
                {localVerificationCode && (
                  <div className="rounded-xl border border-[#72bd91]/30 bg-[#edf8f1] px-3 py-2.5 text-xs text-[#34704d]">
                    本地验证码：
                    <code className="select-all font-bold tracking-[.16em]">
                      {localVerificationCode}
                    </code>
                  </div>
                )}
                <Input
                  key={localVerificationCode || 'email-code'}
                  id="code"
                  name="code"
                  required
                  inputMode="numeric"
                  minLength={6}
                  maxLength={6}
                  pattern="[0-9]{6}"
                  defaultValue={localVerificationCode}
                  placeholder={
                    localVerificationCode
                      ? '输入上方的 6 位验证码'
                      : '输入邮件中的 6 位验证码'
                  }
                  onChange={() => setAuthError('')}
                  className="h-11 rounded-xl border-[#dfe3e9] bg-white"
                />
                <p className="text-[10px] text-[#87909d]">
                  验证码 10 分钟内有效，最多尝试 5 次
                </p>
              </div>
            )}
            {authError && (
              <div
                role="alert"
                className="rounded-xl border border-[#ff6854]/20 bg-[#fff0ed] px-3 py-2.5 text-xs font-semibold text-[#c83b2d]"
              >
                {authError}
              </div>
            )}
            <Button
              type="submit"
              disabled={authLoading}
              className="h-12 w-full rounded-xl bg-[#ff6854] font-bold text-white shadow-[0_8px_20px_rgba(255,104,84,.18)] hover:bg-[#f45e4a]"
            >
              {authLoading
                ? '请稍候…'
                : isReset
                  ? codeSent
                    ? '验证并重置密码'
                    : '发送邮箱验证码'
                  : isRegister
                    ? codeSent
                      ? '验证并创建账号'
                      : '发送邮箱验证码'
                    : '登录控制台'}
            </Button>
          </form>
          <div className="mt-6 text-center text-xs text-[#7d8693]">
            {isReset
              ? '想起密码了？'
              : isRegister
                ? '已有账号？'
                : '还没有账号？'}{' '}
            <button
              onClick={() => {
                setAuthMode(isReset || isRegister ? 'login' : 'register');
                setCodeSent(false);
                setAuthError('');
                setShowPassword(false);
                setShowConfirmPassword(false);
              }}
              className="font-semibold text-[#ef604d]"
            >
              {isReset || isRegister ? '返回登录' : '使用邀请码注册'}
            </button>
          </div>
          <div className="mt-7 border-t border-[#edf0f4] pt-5 text-center text-[10px] leading-5 text-[#9aa2ad]">
            账号凭据与登录状态将加密保存，不会在页面再次展示。
            <br />
            我们不会通过邮件索要你的抖音密码或验证码。
          </div>
        </section>
        <SiteFooter />
        {notice && (
          <div className="toast fixed bottom-7 left-1/2 z-50 flex -translate-x-1/2 items-center gap-2 rounded-full bg-[#222724] px-4 py-2.5 text-xs font-medium text-white shadow-xl">
            <Check className="size-4 text-[#79d19c]" />
            {notice}
          </div>
        )}
      </main>
    );
  }

  const visibleNav = user?.is_admin
    ? [...nav, { label: '我的管理', icon: ShieldCheck }]
    : nav;
  const mobilePrimaryNav = visibleNav.slice(0, 4);
  const plannedFriends =
    primaryTask?.recipients.filter((item) => item.enabled) || [];
  const enabledAccountTasks = accountTasks.filter((task) => task.enabled);
  const allPlannedFriends = accountTasks.flatMap((task) =>
    task.recipients
      .filter((item) => item.enabled)
      .map((item) => ({
        ...item,
        task_id: task.id,
        task_name: task.name,
        send_time: task.send_time,
      })),
  );
  const visiblePlannedFriends = friendsExpanded
    ? allPlannedFriends
    : allPlannedFriends.slice(0, 5);
  const todayAccountRuns = todayRunRecords.filter((run) =>
    accountTaskIds.has(run.task_id),
  );
  const finishedRuns = todayAccountRuns.filter((item) =>
    ['success', 'recovered', 'failed'].includes(item.status),
  );
  const successfulRuns = finishedRuns.filter((item) =>
    ['success', 'recovered'].includes(item.status),
  ).length;
  const successRate = finishedRuns.length
    ? `${Math.round((successfulRuns / finishedRuns.length) * 100)}%`
    : '—';
  const failedRuns = todayAccountRuns.filter((item) =>
    [
      'failed',
      'needs_login',
      'needs_account',
      'security_challenge',
      'needs_review',
    ].includes(item.status),
  ).length;
  const dateText = new Intl.DateTimeFormat('zh-CN', {
    month: 'long',
    day: 'numeric',
    weekday: 'long',
  }).format(new Date());
  const planStartLabel = user?.plan_started_at
    ? chinaTime(user.plan_started_at)
    : '';
  const planExpiryLabel = user?.plan_expires_at
    ? chinaTime(user.plan_expires_at)
    : '';
  const nextRun =
    accountTasks
      .filter((task) => task.enabled)
      .map((task) => utcDate(task.next_run_at))
      .filter((date): date is Date => Boolean(date))
      .sort((a, b) => a.getTime() - b.getTime())[0] || null;
  const nextRunLabel = nextRun
    ? new Intl.DateTimeFormat('zh-CN', {
        timeZone: 'Asia/Shanghai',
        month: 'numeric',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      }).format(nextRun)
    : '尚未安排';
  const todayItemByRecipient = new Map<string, RunItemInfo>();
  todayAccountRuns.forEach((run) =>
    run.items.forEach((item) => {
      const key = `${run.task_id}:${item.contact_name}`;
      if (!todayItemByRecipient.has(key)) todayItemByRecipient.set(key, item);
    }),
  );
  const todaySentFriends = allPlannedFriends.filter(
    (friend) =>
      todayItemByRecipient.get(`${friend.task_id}:${friend.contact_name}`)
        ?.status === 'submitted',
  );
  const todayUnsentFriends = allPlannedFriends.filter(
    (friend) =>
      todayItemByRecipient.get(`${friend.task_id}:${friend.contact_name}`)
        ?.status !== 'submitted',
  );

  return (
    <main className="min-h-screen w-full max-w-full touch-pan-y overflow-x-clip bg-[#f4f1eb] text-[#24221f]">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-[248px] flex-col bg-[#1f2421] px-4 py-5 text-white lg:flex">
        <div className="flex items-center gap-3 px-2">
          <div className="grid size-10 place-items-center rounded-[14px] bg-[#ff6a3d] shadow-[0_8px_24px_rgba(255,106,61,.28)]">
            <Flame className="size-5 fill-white" />
          </div>
          <div>
            <div className="text-[15px] font-bold tracking-wide">火花值守</div>
            <div className="mt-0.5 text-[10px] uppercase tracking-[.18em] text-white/42">
              Spark Keeper
            </div>
          </div>
        </div>

        <nav className="mt-10 space-y-1">
          {visibleNav.map((item) => {
            const Icon = item.icon;
            const selected = active === item.label;
            return (
              <button
                key={item.label}
                onClick={() => setActive(item.label)}
                className={`flex h-11 w-full items-center gap-3 rounded-xl px-3 text-sm transition ${selected ? 'bg-white/[.09] font-semibold text-white' : 'text-white/55 hover:bg-white/[.05] hover:text-white/85'}`}
              >
                <Icon
                  className={`size-[18px] ${selected ? 'text-[#ff805b]' : ''}`}
                />
                {item.label}
                {item.label === '火花好友' && (
                  <span className="ml-auto rounded-full bg-white/10 px-2 py-0.5 text-[10px] text-white/60">
                    {allPlannedFriends.length}
                  </span>
                )}
              </button>
            );
          })}
        </nav>

        <div className="mt-auto rounded-2xl bg-white/[.04] p-1.5">
          <button
            onClick={() => setChangePasswordOpen(true)}
            className="flex w-full items-center gap-3 rounded-xl px-2 py-2 text-left hover:bg-white/[.05]"
            title="账号与密码"
          >
            <Avatar className="size-8 border-0 after:border-0">
              <AvatarFallback className="bg-[#3a4540] text-xs text-white">
                {user?.username?.slice(0, 1).toUpperCase() || 'V'}
              </AvatarFallback>
            </Avatar>
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs font-medium">
                {user?.username || '用户'}
              </div>
              <div className="truncate text-[10px] text-white/38">
                {user?.is_admin
                  ? '管理员权限'
                  : user?.plan_tier === 'pro'
                    ? `Pro ${proPeriodText[user.plan_period || 'year']} · ${planExpiryLabel || '有效期内'}`
                    : user?.plan_tier === 'expired'
                      ? `${expiredPlanName(user)}已到期`
                      : `体验版 · 剩 ${user?.plan_remaining_days ?? '—'} 天`}
              </div>
            </div>
            <KeyRound className="size-4 text-white/35" />
          </button>
          <button
            onClick={logout}
            className="mt-1 flex h-9 w-full items-center justify-center gap-2 rounded-xl text-[11px] text-white/45 hover:bg-white/[.05] hover:text-white/80"
          >
            <LogOut className="size-3.5" />
            退出登录
          </button>
        </div>
      </aside>

      <section className="min-h-screen min-w-0 max-w-full overflow-x-clip pb-[calc(6.25rem+env(safe-area-inset-bottom))] lg:ml-[248px] lg:pb-0">
        <header className="sticky top-0 z-20 flex h-[62px] items-center border-b border-black/[.06] bg-[#f4f1eb]/90 px-4 backdrop-blur-xl md:h-[70px] md:px-8 lg:px-10">
          <button
            onClick={() => setMobileMenuOpen(true)}
            className="mr-3 grid size-9 place-items-center rounded-xl border border-black/[.07] bg-white lg:hidden"
            aria-label="打开菜单"
          >
            <Menu className="size-4" />
          </button>
          <div>
            <h1 className="text-base font-bold tracking-tight">{active}</h1>
            <p className="hidden text-[11px] text-[#7b7770] sm:block">
              今天也会准时守住每一朵火花
            </p>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <Button
              onClick={() => setActive('升级 Pro')}
              className="h-9 rounded-xl bg-[#ff7048] px-3 text-white hover:bg-[#ef603a]"
              aria-label="前往升级 Pro"
            >
              <Crown className="size-4" />
              升级 Pro
            </Button>
            <Button
              onClick={() => setActive('邮件通知')}
              variant="outline"
              size="icon"
              className="relative size-9 rounded-xl border-black/[.07] bg-white/70"
              aria-label="邮件通知"
            >
              <Bell className="size-4" />
              {failedRuns > 0 && (
                <span className="absolute right-2 top-2 size-1.5 rounded-full bg-[#ff6a3d]" />
              )}
            </Button>
          </div>
        </header>

        <div className="mx-auto w-full min-w-0 max-w-[1240px] px-4 py-5 md:px-8 md:py-8 lg:px-10">
          {user?.plan_tier === 'expired' && (
            <div className="mb-5 flex items-start gap-3 rounded-2xl border border-[#e7a276]/35 bg-[#fff0e6] px-4 py-3 text-sm text-[#9b4b2e]">
              <Clock3 className="mt-0.5 size-4 shrink-0" />
              <div>
                <div className="font-bold">{expiredPlanName(user)}已到期</div>
                <div className="mt-0.5 text-xs leading-5 text-[#a9654b]">
                  开始于 {planStartLabel || '—'}，到期于{' '}
                  {planExpiryLabel || '—'}
                  。你仍可查看和管理已有数据，但自动计划不会继续执行。
                </div>
              </div>
            </div>
          )}
          {user?.plan_tier === 'free' && (
            <div className="mb-5 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-2xl border border-[#72bd91]/25 bg-[#edf8f1] px-4 py-3 text-xs text-[#34704d]">
              <span className="font-bold">7 天体验版</span>
              <span>开始：{planStartLabel || '—'}</span>
              <span>到期：{planExpiryLabel || '—'}</span>
              <span>剩余：{user.plan_remaining_days ?? '—'} 天</span>
              <span>最多 5 个计划 · 合计 10 人</span>
            </div>
          )}
          {user?.plan_tier === 'pro' && !user.is_admin && (
            <div className="mb-5 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-2xl border border-[#ff7048]/20 bg-[#fff2ed] px-4 py-3 text-xs text-[#a94b30]">
              <span className="font-bold">
                Pro {proPeriodText[user.plan_period || 'year']}
              </span>
              <span>开始：{planStartLabel || '—'}</span>
              <span>到期：{planExpiryLabel || '—'}</span>
              <span>最多 10 个计划 · 合计 25 人</span>
            </div>
          )}
          {active === '总览' && (
            <div className="w-full min-w-0 max-w-full">
              <div className="mb-6 flex w-full min-w-0 max-w-full flex-col justify-between gap-4 sm:flex-row sm:items-end">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 text-xs font-semibold text-[#ff6540]">
                    <Sparkles className="size-3.5" />
                    {dateText}
                  </div>
                  <h2 className="mt-2 text-[26px] font-bold tracking-[-.035em] md:text-[30px]">
                    {accountTasks.length
                      ? '值守计划已准备好。'
                      : '从绑定抖音账号开始。'}
                  </h2>
                  <p className="mt-1 text-sm text-[#807b73]">
                    {accountTasks.length
                      ? `${allPlannedFriends.length} 位好友分布在 ${accountTasks.length} 个计划中，下次执行：${nextRunLabel}。`
                      : '当前没有已保存的执行计划。'}
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-[11px] text-[#979087]">
                    每 15 秒自动更新
                  </span>
                </div>
              </div>

              <div className="grid w-full min-w-0 max-w-full grid-cols-[minmax(0,1fr)] gap-4 md:grid-cols-3">
                <article className="relative w-full min-w-0 max-w-full overflow-hidden rounded-[22px] bg-[#ff7048] p-5 text-white shadow-[0_16px_36px_rgba(211,79,42,.18)] md:col-span-2">
                  <div className="absolute -right-12 -top-20 size-52 rounded-full border-[34px] border-white/[.07]" />
                  <div className="relative flex min-h-[162px] flex-col justify-between">
                    <div className="flex min-w-0 items-start justify-between gap-3">
                      <div className="flex min-w-0 items-center gap-3">
                        <Avatar className="size-12 shrink-0 border-2 border-white/25 after:border-0">
                          <AvatarFallback className="bg-[#292c29] font-bold text-white">
                            抖
                          </AvatarFallback>
                          <AvatarBadge className="size-3.5! bg-[#6bd295] ring-[#ff7048]" />
                        </Avatar>
                        <div className="min-w-0">
                          <div className="flex min-w-0 items-center gap-2">
                            <h3 className="min-w-0 truncate font-bold">
                              {selectedAccount?.name || '尚未绑定抖音账号'}
                            </h3>
                            <Badge className="shrink-0 border-0 bg-white/16 text-[10px] text-white">
                              {statusText[selectedAccount?.status || 'unbound']}
                            </Badge>
                          </div>
                          <p className="mt-1 truncate text-xs text-white/68">
                            {selectedAccount?.bound_at
                              ? `绑定时间：${chinaTime(selectedAccount.bound_at)}`
                              : '绑定后才能执行自动任务'}
                          </p>
                        </div>
                      </div>
                      <button
                        onClick={() => setActive('账号绑定')}
                        className="grid size-8 shrink-0 place-items-center rounded-full bg-white/12 hover:bg-white/20"
                        aria-label="账号设置"
                      >
                        <MoreHorizontal className="size-4" />
                      </button>
                    </div>
                    <div className="grid min-w-0 grid-cols-3 gap-3 sm:gap-4">
                      <div className="min-w-0">
                        <div className="text-2xl font-bold">
                          {allPlannedFriends.length}
                        </div>
                        <div className="text-[11px] leading-4 text-white/62">
                          续火好友
                        </div>
                      </div>
                      <div className="min-w-0">
                        <div className="text-2xl font-bold">
                          {successfulRuns}
                        </div>
                        <div className="text-[11px] leading-4 text-white/62">
                          最近成功任务
                        </div>
                      </div>
                      <div className="min-w-0">
                        <div className="text-2xl font-bold">{successRate}</div>
                        <div className="text-[11px] leading-4 text-white/62">
                          最近成功率
                        </div>
                      </div>
                    </div>
                  </div>
                </article>

                <article className="w-full min-w-0 max-w-full rounded-[22px] border border-black/[.06] bg-[#fffdf9] p-5 shadow-[0_12px_28px_rgba(35,32,27,.04)]">
                  <div className="flex items-center justify-between">
                    <div className="grid size-9 place-items-center rounded-xl bg-[#eceae4]">
                      <Clock3 className="size-[18px]" />
                    </div>
                    <Badge
                      variant="outline"
                      className={
                        accountTasks.some((task) => task.enabled)
                          ? 'border-[#72bd91]/35 bg-[#e9f6ee] text-[#34704d]'
                          : 'border-black/10 bg-[#f0eee9] text-[#817b72]'
                      }
                    >
                      {accountTasks.length
                        ? accountTasks.some((task) => task.enabled)
                          ? '等待执行'
                          : '已暂停'
                        : '未创建'}
                    </Badge>
                  </div>
                  <p className="mt-5 text-xs text-[#868078]">下一次执行</p>
                  <div className="mt-1 break-words text-[24px] font-bold tracking-[-.04em]">
                    {nextRunLabel}
                  </div>
                  <div className="mt-4 flex items-center gap-2 text-xs text-[#756f67]">
                    <span className="size-2 rounded-full bg-[#ff7048]" />
                    {accountTasks.length
                      ? `${accountTasks.length} 个计划已保存`
                      : '请先创建执行计划'}
                  </div>
                </article>
              </div>

              <div className="mt-4 grid w-full min-w-0 max-w-full grid-cols-[minmax(0,1fr)] gap-4 xl:grid-cols-[minmax(0,1.42fr)_minmax(0,.88fr)]">
                <article className="w-full min-w-0 max-w-full rounded-[22px] border border-black/[.06] bg-[#fffdf9] shadow-[0_12px_28px_rgba(35,32,27,.04)]">
                  <div className="flex min-w-0 items-center justify-between gap-3 border-b border-black/[.055] px-5 py-4">
                    <div className="min-w-0">
                      <h3 className="text-sm font-bold">计划中的火花好友</h3>
                      <p className="mt-0.5 break-words text-[11px] text-[#928b82]">
                        共 {allPlannedFriends.length} 人，来自{' '}
                        {accountTasks.length} 个已保存计划
                      </p>
                    </div>
                    <Button
                      onClick={() => setActive('火花好友')}
                      variant="ghost"
                      size="sm"
                      className="shrink-0 text-[#f05f39] hover:bg-[#fff0eb] hover:text-[#e5532e]"
                    >
                      管理好友 <ChevronRight />
                    </Button>
                  </div>
                  <div className="divide-y divide-black/[.05] px-5">
                    {visiblePlannedFriends.length ? (
                      visiblePlannedFriends.map((friend) => (
                        <div
                          key={friend.id}
                          className="flex min-w-0 items-center gap-3 py-3.5"
                        >
                          <Avatar className="size-10 shrink-0 after:border-black/[.05]">
                            <AvatarFallback className="bg-[#f3b467] font-bold text-[#342d27]">
                              {friend.contact_name.slice(0, 1)}
                            </AvatarFallback>
                          </Avatar>
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-sm font-semibold">
                              {friend.contact_name}
                            </div>
                            <div className="mt-0.5 truncate text-[11px] text-[#918a81]">
                              {friend.task_name} · {friend.send_time} · 消息：
                              {friend.message}
                            </div>
                          </div>
                          <div className="flex shrink-0 items-center gap-1.5 text-xs font-medium text-[#4f8a65]">
                            <Check className="size-3.5" />
                            已保存
                          </div>
                        </div>
                      ))
                    ) : (
                      <div className="py-14 text-center text-sm text-[#8e887f]">
                        还没有好友，创建计划后会显示在这里。
                      </div>
                    )}
                  </div>
                  {allPlannedFriends.length > 5 && (
                    <button
                      type="button"
                      onClick={() => setFriendsExpanded((value) => !value)}
                      className="flex w-full items-center justify-center gap-1.5 border-t border-black/[.055] py-3 text-xs font-semibold text-[#d95739]"
                    >
                      {friendsExpanded ? (
                        <ChevronUp className="size-4" />
                      ) : (
                        <ChevronDown className="size-4" />
                      )}
                      {friendsExpanded
                        ? '收起'
                        : `展开全部 ${allPlannedFriends.length} 人`}
                    </button>
                  )}
                </article>

                <div className="w-full min-w-0 max-w-full space-y-4">
                  <article className="w-full min-w-0 max-w-full rounded-[22px] border border-black/[.06] bg-[#fffdf9] p-5 shadow-[0_12px_28px_rgba(35,32,27,.04)]">
                    <div className="flex min-w-0 items-start justify-between gap-3">
                      <div className="min-w-0">
                        <h3 className="text-sm font-bold">自动值守</h3>
                        <p className="mt-1 break-words text-[11px] text-[#8e887f]">
                          {accountTasks.length
                            ? `${enabledAccountTasks.length}/${accountTasks.length} 个计划已开启`
                            : '创建计划后可开启'}
                        </p>
                      </div>
                      <Switch
                        checked={enabledAccountTasks.length > 0}
                        onCheckedChange={(value) =>
                          void toggleAllAccountTasks(value)
                        }
                        className="shrink-0 data-checked:bg-[#ff7048]"
                      />
                    </div>
                    <div className="mt-5 rounded-2xl bg-[#f4f0e9] p-4">
                      <div className="flex min-w-0 items-center justify-between gap-3">
                        <span className="shrink-0 text-xs text-[#7d766d]">
                          下次执行
                        </span>
                        <span className="min-w-0 break-words text-right text-sm font-bold">
                          {nextRunLabel}
                        </span>
                      </div>
                      <div className="my-3 h-px bg-black/[.06]" />
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-xs text-[#7d766d]">好友数量</span>
                        <span className="text-sm font-bold">
                          {allPlannedFriends.length} 位
                        </span>
                      </div>
                    </div>
                    <Button
                      variant="outline"
                      className="mt-4 h-9 w-full rounded-xl border-black/[.08] bg-white"
                      onClick={() => setActive('执行计划')}
                    >
                      <Settings />
                      调整计划
                    </Button>
                  </article>
                  <article className="w-full min-w-0 max-w-full rounded-[22px] bg-[#252a27] p-5 text-white shadow-[0_12px_28px_rgba(22,24,22,.12)]">
                    <div className="flex min-w-0 items-center justify-between gap-3">
                      <div className="grid size-9 shrink-0 place-items-center rounded-xl bg-white/10">
                        <Gauge className="size-[18px] text-[#ff8b69]" />
                      </div>
                      <span className="min-w-0 text-right text-[10px] text-white/38">
                        服务器真实队列
                      </span>
                    </div>
                    <div className="mt-4 flex min-w-0 flex-wrap items-baseline gap-2">
                      <span className="text-2xl font-bold">
                        {queue.position
                          ? `第 ${queue.position} 位`
                          : '当前空闲'}
                      </span>
                      {queue.position && (
                        <span className="text-xs text-white/45">
                          预计等待 {queue.waiting_minutes} 分钟
                        </span>
                      )}
                    </div>
                    <div className="mt-4 text-xs text-white/45">
                      全站等待/运行任务：{queue.queued} 个
                    </div>
                  </article>
                </div>
              </div>

              <div className="mt-4 grid w-full min-w-0 max-w-full grid-cols-[minmax(0,1fr)] gap-4 md:grid-cols-2">
                <article className="w-full min-w-0 max-w-full rounded-[22px] border border-[#72bd91]/25 bg-[#f4fbf6] p-5">
                  <div className="flex min-w-0 items-center justify-between gap-3">
                    <h3 className="min-w-0 text-sm font-bold text-[#34704d]">
                      今日已发送信息
                    </h3>
                    <Badge
                      variant="outline"
                      className="shrink-0 border-[#72bd91]/35 bg-white text-[#34704d]"
                    >
                      {todaySentFriends.length} 人
                    </Badge>
                  </div>
                  <div className="mt-3 flex min-w-0 flex-wrap gap-2">
                    {todaySentFriends.length ? (
                      todaySentFriends.map((friend) => (
                        <span
                          key={`${friend.task_id}:${friend.id}`}
                          className="max-w-full break-words rounded-full bg-white px-3 py-1.5 text-xs text-[#34704d]"
                        >
                          {friend.contact_name} · {friend.task_name}
                        </span>
                      ))
                    ) : (
                      <span className="text-xs text-[#72907d]">
                        今天还没有确认发送成功的信息
                      </span>
                    )}
                  </div>
                </article>
                <article className="w-full min-w-0 max-w-full rounded-[22px] border border-[#e7a742]/25 bg-[#fffaf0] p-5">
                  <div className="flex min-w-0 items-center justify-between gap-3">
                    <h3 className="min-w-0 text-sm font-bold text-[#94631d]">
                      今日未发送信息
                    </h3>
                    <Badge
                      variant="outline"
                      className="shrink-0 border-[#e7a742]/35 bg-white text-[#94631d]"
                    >
                      {todayUnsentFriends.length} 人
                    </Badge>
                  </div>
                  <div className="mt-3 flex min-w-0 flex-wrap gap-2">
                    {todayUnsentFriends.length ? (
                      todayUnsentFriends.map((friend) => (
                        <span
                          key={`${friend.task_id}:${friend.id}`}
                          className="max-w-full break-words rounded-full bg-white px-3 py-1.5 text-xs text-[#94631d]"
                        >
                          {friend.contact_name} · {friend.task_name}
                        </span>
                      ))
                    ) : (
                      <span className="text-xs text-[#8e866f]">
                        今天的计划均已确认发送
                      </span>
                    )}
                  </div>
                </article>
              </div>
            </div>
          )}

          {active === '账号绑定' && (
            <div>
              <div className="mb-6">
                <p className="text-xs font-semibold text-[#ff6540]">账号连接</p>
                <h2 className="mt-2 text-[26px] font-bold tracking-[-.035em]">
                  管理你的抖音账号
                </h2>
                <p className="mt-1 text-sm text-[#807b73]">
                  一个网站账号最多绑定 5
                  个抖音账号。好友、计划和运行记录按当前账号分别管理，服务器仍会逐个排队执行。
                </p>
              </div>
              <article className="mb-4 rounded-[22px] border border-black/[.06] bg-[#fffdf9] p-5 shadow-[0_12px_28px_rgba(35,32,27,.04)]">
                <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
                  <div>
                    <h3 className="text-sm font-bold">我的抖音账号</h3>
                    <p className="mt-1 text-[11px] text-[#8e887f]">
                      点击账号即可切换当前管理对象（{accounts.length}/5）
                    </p>
                  </div>
                  <div className="flex flex-col gap-2 sm:flex-row">
                    <Input
                      value={newAccountName}
                      onChange={(event) =>
                        setNewAccountName(event.target.value)
                      }
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') void addAccount();
                      }}
                      maxLength={80}
                      placeholder="备注名，例如：工作号"
                      className="h-11 w-full rounded-xl border-[#dfe3e9] sm:h-10 sm:w-52"
                    />
                    <Button
                      onClick={addAccount}
                      disabled={creatingAccount || accounts.length >= 5}
                      className="h-11 w-full rounded-xl bg-[#282d29] text-white hover:bg-[#3b423d] sm:h-10 sm:w-auto"
                    >
                      <Plus className="size-4" />
                      {creatingAccount ? '添加中…' : '添加账号'}
                    </Button>
                  </div>
                </div>
                <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                  {accounts.length ? (
                    accounts.map((account) => (
                      <div
                        key={account.id}
                        className={`flex items-center rounded-2xl border p-1.5 transition ${selectedAccount?.id === account.id ? 'border-[#ff7048] bg-[#fff1ec]' : 'border-black/[.07] bg-white hover:border-[#ff7048]/40'}`}
                      >
                        <button
                          onClick={() => {
                            setSelectedAccountId(account.id);
                            setBinding(null);
                          }}
                          className="flex min-w-0 flex-1 items-center gap-3 rounded-xl p-2 text-left"
                        >
                          <Avatar className="size-9 shrink-0 after:border-black/[.05]">
                            <AvatarFallback className="bg-[#282d29] text-xs font-bold text-white">
                              抖
                            </AvatarFallback>
                          </Avatar>
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-sm font-semibold">
                              {account.name}
                            </div>
                            <div className="mt-0.5 text-[10px] text-[#8e887f]">
                              {statusText[account.status] || account.status}
                            </div>
                            <div className="mt-0.5 truncate text-[10px] text-[#a19a91]">
                              绑定时间：
                              {account.bound_at
                                ? chinaTime(account.bound_at)
                                : '尚未完成绑定'}
                            </div>
                          </div>
                          {selectedAccount?.id === account.id && (
                            <Check className="size-4 shrink-0 text-[#ef604d]" />
                          )}
                        </button>
                        <button
                          onClick={() => setAccountPendingDelete(account)}
                          className="grid size-10 shrink-0 place-items-center rounded-xl text-[#a79f96] transition hover:bg-[#fff0eb] hover:text-[#d95739]"
                          aria-label={`删除${account.name}`}
                          title="删除账号"
                        >
                          <Trash2 className="size-4" />
                        </button>
                      </div>
                    ))
                  ) : (
                    <div className="rounded-2xl border border-dashed border-black/10 px-4 py-6 text-center text-xs text-[#8e887f] sm:col-span-2 xl:col-span-3">
                      还没有抖音账号，请先填写备注名并添加。
                    </div>
                  )}
                </div>
              </article>
              <div className="grid gap-4 lg:grid-cols-[1.05fr_.95fr]">
                <article className="rounded-[22px] border border-black/[.06] bg-[#fffdf9] p-6 shadow-[0_12px_28px_rgba(35,32,27,.04)]">
                  <div className="flex items-center gap-4">
                    <Avatar className="size-14 after:border-black/[.05]">
                      <AvatarFallback className="bg-[#282d29] font-bold text-white">
                        抖
                      </AvatarFallback>
                    </Avatar>
                    <div className="min-w-0 flex-1">
                      <h3 className="font-bold">
                        {selectedAccount?.name || '请先添加账号'}
                      </h3>
                      <p className="mt-1 text-xs text-[#8e887f]">
                        状态：{statusText[selectedAccount?.status || 'unbound']}
                      </p>
                    </div>
                    <Badge
                      variant="outline"
                      className={
                        selectedAccount?.status === 'valid'
                          ? 'border-[#72bd91]/35 bg-[#e9f6ee] text-[#34704d]'
                          : 'border-[#ff7650]/30 bg-[#fff2ed] text-[#e85b36]'
                      }
                    >
                      {statusText[selectedAccount?.status || 'unbound']}
                    </Badge>
                  </div>
                  <div className="mt-6 rounded-2xl bg-[#f4f0e9] p-4 text-xs leading-6 text-[#756f67]">
                    每次绑定都需要短信验证：先使用抖音 App
                    扫描右侧二维码并授权；网站会自动选择“接收短信验证码”并触发短信，收到后在下方输入即可完成绑定。
                  </div>
                  {binding?.status === 'verification_required' && (
                    <div className="mt-4 rounded-2xl border border-[#ff7048]/25 bg-[#fff3ee] p-4">
                      <Label
                        htmlFor="douyin-verification-code"
                        className="text-xs font-bold text-[#d95739]"
                      >
                        抖音短信验证码
                      </Label>
                      <p className="mt-1 text-[11px] leading-5 text-[#8c6c63]">
                        {binding.error_message ||
                          '短信已由抖音发送，请输入你本人收到的验证码。'}
                      </p>
                      <div className="mt-3 flex gap-2">
                        <Input
                          id="douyin-verification-code"
                          inputMode="numeric"
                          autoComplete="one-time-code"
                          maxLength={6}
                          value={douyinVerificationCode}
                          onChange={(event) =>
                            setDouyinVerificationCode(
                              event.target.value.replace(/\D/g, '').slice(0, 6),
                            )
                          }
                          placeholder="4—6 位验证码"
                          className="h-11 flex-1 rounded-xl border-[#f0c8bd] bg-white text-center tracking-[.25em]"
                        />
                        <Button
                          onClick={submitDouyinVerification}
                          disabled={
                            verificationLoading ||
                            douyinVerificationCode.length < 4
                          }
                          className="h-11 rounded-xl bg-[#ff7048] px-4 text-white hover:bg-[#ef603a]"
                        >
                          {verificationLoading ? '提交中…' : '提交验证'}
                        </Button>
                      </div>
                    </div>
                  )}
                  {binding?.status === 'verification_submitted' && (
                    <div className="mt-4 rounded-2xl border border-[#e5c36c]/30 bg-[#fff8dd] p-4 text-xs leading-5 text-[#806724]">
                      <div className="font-bold">
                        验证码已收到，正在抖音页面确认登录
                      </div>
                      <div className="mt-1">
                        通常需要 3—15
                        秒；若抖音没有接受，输入框会重新出现并提示你重试，请勿刷新或重新生成二维码。
                      </div>
                    </div>
                  )}
                  <Button
                    onClick={startBinding}
                    disabled={
                      !selectedAccount ||
                      accountLoading ||
                      [
                        'pending',
                        'running',
                        'qr_ready',
                        'verification_required',
                        'verification_submitted',
                      ].includes(binding?.status || '')
                    }
                    className="mt-5 h-11 w-full rounded-xl bg-[#282d29] text-white hover:bg-[#3b423d]"
                  >
                    <QrCode className="size-4" />
                    {accountLoading
                      ? '正在创建会话…'
                      : selectedAccount?.status === 'valid'
                        ? '重新扫码绑定当前账号'
                        : '开始扫码绑定当前账号'}
                  </Button>
                  {binding &&
                    [
                      'pending',
                      'running',
                      'qr_ready',
                      'verification_required',
                      'verification_submitted',
                    ].includes(binding.status) && (
                      <div className="mt-2 grid grid-cols-2 gap-2">
                        <Button
                          onClick={cancelBinding}
                          disabled={accountLoading}
                          variant="outline"
                          className="h-11 rounded-xl border-black/10 bg-white text-[#6f6a63]"
                        >
                          <X className="size-4" />
                          取消绑定
                        </Button>
                        <Button
                          onClick={restartBinding}
                          disabled={accountLoading}
                          variant="outline"
                          className="h-11 rounded-xl border-[#ff7048]/25 bg-white text-[#d95739]"
                        >
                          <RefreshCw className="size-4" />
                          {accountLoading ? '正在重新打开…' : '重新生成二维码'}
                        </Button>
                      </div>
                    )}
                </article>
                <article className="grid min-h-[330px] place-items-center rounded-[22px] bg-[#252a27] p-6 text-center text-white shadow-[0_12px_28px_rgba(22,24,22,.12)]">
                  {binding?.qr_data_url ? (
                    <div>
                      <img
                        src={binding.qr_data_url}
                        alt="抖音登录二维码"
                        className="mx-auto size-56 rounded-2xl bg-white p-3"
                      />
                      <p className="mt-4 text-sm font-semibold">
                        请使用抖音 App 扫码并在手机确认
                      </p>
                      <p className="mt-1 text-xs text-white/45">
                        异常或过期时请点击左侧“重新生成二维码”
                      </p>
                    </div>
                  ) : (
                    <div>
                      <div className="mx-auto grid size-16 place-items-center rounded-2xl bg-white/10">
                        <QrCode className="size-8 text-[#ff805b]" />
                      </div>
                      <p className="mt-5 font-semibold">
                        {binding?.status === 'error' ||
                        binding?.status === 'expired'
                          ? binding.error_message || '扫码会话已结束'
                          : binding?.status === 'verification_required'
                            ? '请先在左侧输入短信验证码'
                            : binding?.status === 'verification_submitted'
                              ? '正在验证登录…'
                              : binding?.status === 'pending'
                                ? '等待浏览器空闲及服务器资源恢复'
                                : binding
                                  ? '正在加载二维码…'
                                  : '二维码尚未生成'}
                      </p>
                      <p className="mt-2 text-xs text-white/45">
                        {binding?.status === 'verification_required'
                          ? '验证码由你本人填写，系统不会读取你的短信'
                          : '绑定会话约 5 分钟，过期后可重新生成'}
                      </p>
                    </div>
                  )}
                  {binding &&
                    [
                      'pending',
                      'running',
                      'qr_ready',
                      'verification_required',
                      'verification_submitted',
                    ].includes(binding.status) && (
                      <div className="mt-4 rounded-full bg-white/10 px-4 py-2 text-xs text-white/65">
                        剩余 {bindingSeconds} 秒
                      </div>
                    )}
                </article>
              </div>
            </div>
          )}

          {active === '火花好友' && (
            <div>
              <div className="mb-6 flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
                <div>
                  <p className="text-xs font-semibold text-[#ff6540]">
                    会话管理 · 当前账号：{selectedAccount?.name || '未选择'}
                  </p>
                  <h2 className="mt-2 text-[26px] font-bold tracking-[-.035em]">
                    选择要守护的火花
                  </h2>
                  <p className="mt-1 text-sm text-[#807b73]">
                    每个抖音账号的好友、群聊和计划独立保存，切换账号不会混在一起。
                  </p>
                </div>
                <Button
                  onClick={syncContacts}
                  disabled={
                    contactLoading || selectedAccount?.status !== 'valid'
                  }
                  variant="outline"
                  className="h-10 rounded-xl border-black/[.08] bg-white"
                >
                  <RefreshCw
                    className={`size-4 ${contactLoading ? 'animate-spin' : ''}`}
                  />
                  {contactLoading
                    ? '正在读取…'
                    : contacts.length
                      ? '重新同步会话'
                      : '同步最近会话'}
                </Button>
              </div>
              <article className="mb-4 rounded-[22px] border border-black/[.06] bg-[#fffdf9] p-5 shadow-[0_12px_28px_rgba(35,32,27,.04)]">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div>
                    <Label className="text-xs font-bold">
                      先选择要编辑的计划
                    </Label>
                    <p className="mt-1 text-[10px] text-[#9a938a]">
                      每个计划独立保存 1—5
                      位好友；切换计划时会完整换成该计划的数据
                    </p>
                  </div>
                  <span className="text-[11px] text-[#8e887f]">
                    当前已选 {selectedRecipients.length}/5 ·{' '}
                    {contactsDirty
                      ? '有未保存修改'
                      : primaryTask
                        ? '已保存'
                        : '新计划未创建'}
                  </span>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  {accountTasks.map((task, index) => (
                    <button
                      key={task.id}
                      type="button"
                      onClick={() => setSelectedTaskId(task.id)}
                      className={`rounded-xl border px-3 py-2 text-xs ${primaryTask?.id === task.id ? 'border-[#ff7048] bg-[#fff1ec] font-semibold text-[#d95739]' : 'border-black/[.07] bg-white'}`}
                    >
                      计划 {index + 1} · {task.recipients.length}/5 人
                    </button>
                  ))}
                  {canCreateTask && (
                    <button
                      type="button"
                      onClick={() => setSelectedTaskId('__new__')}
                      className={`rounded-xl border border-dashed px-3 py-2 text-xs ${!primaryTask ? 'border-[#ff7048] bg-[#fff8f5] font-semibold text-[#d95739]' : 'border-black/15 bg-white text-[#777168]'}`}
                    >
                      <Plus className="mr-1 inline size-3.5" />
                      新建计划
                    </button>
                  )}
                </div>
                <div className="mt-5 flex flex-col gap-3 border-t border-black/[.055] pt-4 sm:flex-row sm:items-center sm:justify-between">
                  <div>
                    <Label className="text-xs font-bold">最近会话</Label>
                    <p className="mt-1 text-[10px] text-[#9a938a]">
                      {contactsSyncedAt
                        ? `上次同步：${chinaTime(contactsSyncedAt)}`
                        : '尚未同步'}
                    </p>
                  </div>
                  <span className="text-[10px] text-[#9a938a]">
                    全部账号已安排 {totalAssignedRecipients}/
                    {user?.limits.recipients_total || 0} 人 · {tasks.length}/
                    {user?.limits.tasks_total || 0} 个计划
                  </span>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  {(
                    [
                      ['all', '全部'],
                      ['friend', '好友'],
                      ['group', '群聊'],
                      ['unknown', '待识别'],
                    ] as const
                  ).map(([value, label]) => (
                    <button
                      key={value}
                      type="button"
                      onClick={() => {
                        setContactFilter(value);
                        setContactsExpanded(false);
                      }}
                      className={`rounded-full border px-3 py-1.5 text-[11px] ${contactFilter === value ? 'border-[#ff7048] bg-[#fff1ec] font-semibold text-[#d95739]' : 'border-black/[.07] bg-white text-[#777168]'}`}
                    >
                      {label}{' '}
                      {value === 'all'
                        ? contacts.length
                        : contacts.filter(
                            (contact) => contact.conversation_type === value,
                          ).length}
                    </button>
                  ))}
                </div>
                <div className="mt-3 grid min-h-24 gap-2 sm:grid-cols-2 xl:grid-cols-3">
                  {visibleFilteredContacts.length ? (
                    visibleFilteredContacts.map((contact) => {
                      const checked = selectedRecipients.some(
                        (item) => item.contact_id === contact.id,
                      );
                      const assignedTask = assignedTaskByContact.get(
                        contact.id,
                      );
                      const assignedElsewhere = Boolean(
                        assignedTask && assignedTask.id !== primaryTask?.id,
                      );
                      return (
                        <div
                          key={contact.id}
                          className={`rounded-xl border px-3 py-3 text-sm transition ${checked ? 'border-[#ff7048] bg-[#fff1ec]' : assignedElsewhere ? 'border-black/[.05] bg-[#f3f0ea]' : 'border-black/[.07] bg-white hover:border-[#ff7048]/40'}`}
                        >
                          <button
                            type="button"
                            onClick={() => toggleContact(contact)}
                            className="flex w-full items-center gap-3 text-left"
                          >
                            <span
                              className={`grid size-5 shrink-0 place-items-center rounded-md border ${checked ? 'border-[#ff7048] bg-[#ff7048] text-white' : 'border-black/15'}`}
                            >
                              {checked && <Check className="size-3.5" />}
                            </span>
                            <span className="min-w-0 flex-1">
                              <span
                                className={`block truncate ${checked ? 'font-semibold text-[#d95739]' : ''}`}
                              >
                                {contact.display_name}
                              </span>
                              <span className="mt-0.5 block text-[10px] font-normal text-[#938c83]">
                                {assignedElsewhere
                                  ? `已在${assignedTask?.name}`
                                  : contact.streak_days != null
                                    ? `火花 ${contact.streak_days} 天`
                                    : '未安排'}
                              </span>
                            </span>
                          </button>
                          <select
                            aria-label={`设置${contact.display_name}的会话类型`}
                            value={contact.conversation_type}
                            onChange={(event) =>
                              void updateContactType(
                                contact,
                                event.target.value as ConversationType,
                              )
                            }
                            className="mt-2 h-8 w-full rounded-lg border border-black/10 bg-white px-2 text-[11px] text-[#756f67]"
                          >
                            <option value="friend">好友</option>
                            <option value="group">群聊</option>
                            <option value="unknown">待识别</option>
                          </select>
                        </div>
                      );
                    })
                  ) : (
                    <div className="grid place-items-center rounded-xl border border-dashed border-black/10 px-4 py-8 text-center text-xs leading-5 text-[#8e887f] sm:col-span-2 xl:col-span-3">
                      {contacts.length
                        ? '当前分类中没有会话。'
                        : selectedAccount?.status === 'valid'
                          ? '点击“同步最近会话”，读取完成后会自动保存并在刷新后恢复。'
                          : '请先到“账号绑定”完成当前抖音账号的扫码登录。'}
                    </div>
                  )}
                </div>
                {filteredContacts.length > 12 && (
                  <button
                    type="button"
                    onClick={() => setContactsExpanded((value) => !value)}
                    className="mt-3 flex w-full items-center justify-center gap-1.5 rounded-xl border border-black/[.07] bg-white py-2.5 text-xs font-semibold text-[#d95739]"
                  >
                    {contactsExpanded ? (
                      <ChevronUp className="size-4" />
                    ) : (
                      <ChevronDown className="size-4" />
                    )}
                    {contactsExpanded
                      ? '收起最近会话'
                      : `展开全部 ${filteredContacts.length} 个会话`}
                  </button>
                )}
                {selectedRecipients.length > 0 && (
                  <div className="mt-5 space-y-3 border-t border-black/[.055] pt-5">
                    <div className="text-xs font-bold">每个会话的消息</div>
                    {selectedRecipients.map((recipient, index) => (
                      <div
                        key={`${primaryTask?.id || 'new'}:${recipient.contact_id || recipient.contact_name}:${index}`}
                        className="grid gap-2 rounded-xl bg-[#f5f2ec] p-3 sm:grid-cols-[150px_1fr]"
                      >
                        <div className="min-w-0">
                          <div className="truncate text-xs font-semibold">
                            {recipient.contact_name}
                          </div>
                          <div className="mt-1 text-[10px] text-[#8e887f]">
                            {conversationLabel[recipient.conversation_type]}
                          </div>
                        </div>
                        <Input
                          aria-label={`${recipient.contact_name}的消息`}
                          value={recipient.message}
                          onChange={(event) =>
                            updateRecipientMessage(index, event.target.value)
                          }
                          maxLength={500}
                          className="h-10 rounded-xl border-[#dfe3e9] bg-white"
                        />
                      </div>
                    ))}
                  </div>
                )}
                <Button
                  onClick={saveContactSelection}
                  disabled={
                    contactsSaving || (Boolean(primaryTask) && !contactsDirty)
                  }
                  className="mt-5 h-11 w-full rounded-xl bg-[#282d29] text-white hover:bg-[#3b423d]"
                >
                  <Check className="size-4" />
                  {contactsSaving
                    ? '保存中…'
                    : primaryTask
                      ? contactsDirty
                        ? `保存到${primaryTask.name}`
                        : `${primaryTask.name}已保存`
                      : '下一步：设置时间并创建计划'}
                </Button>
                <p className="mt-3 text-[11px] leading-5 text-[#8e887f]">
                  同步会自动区分私聊和明显群聊；判断不准时可直接用每张卡片下方的类型选项修正。已加入其他计划的好友会锁定，避免一天重复发送。
                </p>
              </article>
              <article className="overflow-hidden rounded-[22px] border border-black/[.06] bg-[#fffdf9] shadow-[0_12px_28px_rgba(35,32,27,.04)]">
                <div className="grid grid-cols-[1fr_auto] border-b border-black/[.055] px-5 py-4 text-[11px] font-medium text-[#8e887f] sm:grid-cols-[1fr_140px_120px]">
                  {' '}
                  <span>好友</span>
                  <span className="hidden sm:block">连续天数</span>
                  <span>今日计划</span>
                </div>
                {plannedFriends.length ? (
                  plannedFriends.map((friend) => (
                    <div
                      key={friend.id}
                      className="grid grid-cols-[1fr_auto] items-center gap-4 border-b border-black/[.05] px-5 py-4 last:border-0 sm:grid-cols-[1fr_140px_120px]"
                    >
                      <div className="flex items-center gap-3">
                        <Avatar className="size-10 after:border-black/[.05]">
                          <AvatarFallback className="bg-[#f3b467] font-bold text-[#342d27]">
                            {friend.contact_name.slice(0, 1)}
                          </AvatarFallback>
                        </Avatar>
                        <div>
                          <div className="text-sm font-semibold">
                            {friend.contact_name}
                          </div>
                          <div className="max-w-72 truncate text-[11px] text-[#918a81]">
                            {friend.message}
                          </div>
                        </div>
                      </div>
                      <span className="hidden text-sm font-semibold sm:block">
                        已保存
                      </span>
                      <Badge
                        variant="outline"
                        className="border-[#72bd91]/35 bg-[#e9f6ee] text-[#34704d]"
                      >
                        已加入
                      </Badge>
                    </div>
                  ))
                ) : (
                  <div className="px-5 py-14 text-center text-sm text-[#8e887f]">
                    尚未保存好友。请在上方填写昵称和消息，然后前往执行计划保存。
                  </div>
                )}
              </article>
            </div>
          )}

          {active === '执行计划' && (
            <div>
              <div className="mb-6">
                <p className="text-xs font-semibold text-[#ff6540]">错峰调度</p>
                <h2 className="mt-2 text-[26px] font-bold tracking-[-.035em]">
                  设置你的值守时间
                </h2>
                <p className="mt-1 text-sm text-[#807b73]">
                  时间可以任意选择；保存前会校验前后 15 分钟的全站队列负载。
                </p>
              </div>
              <article className="mb-4 rounded-[22px] border border-black/[.06] bg-[#fffdf9] p-4 shadow-[0_12px_28px_rgba(35,32,27,.04)]">
                <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
                  <div>
                    <h3 className="text-sm font-bold">当前账号的执行计划</h3>
                    <p className="mt-1 text-[11px] text-[#8e887f]">
                      当前账号 {accountTasks.length} 个 · 全部账号{' '}
                      {tasks.length}/{user?.limits.tasks_total || 0} 个 ·
                      每个计划最多 5 人 · 合计最多{' '}
                      {user?.limits.recipients_total || 0} 人
                    </p>
                  </div>
                  {primaryTask && (
                    <Button
                      onClick={deleteTask}
                      variant="outline"
                      className="h-9 rounded-xl border-[#ef604d]/20 bg-white text-[#d95739]"
                    >
                      <Trash2 className="size-4" />
                      删除计划
                    </Button>
                  )}
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  {accountTasks.map((task, index) => (
                    <button
                      key={task.id}
                      onClick={() => setSelectedTaskId(task.id)}
                      className={`rounded-xl border px-3 py-2 text-xs ${primaryTask?.id === task.id ? 'border-[#ff7048] bg-[#fff1ec] font-semibold text-[#d95739]' : 'border-black/[.07] bg-white'}`}
                    >
                      计划 {index + 1} · {task.send_time} ·{' '}
                      {task.recipients.length} 人
                    </button>
                  ))}
                  {canCreateTask && (
                    <button
                      onClick={() => {
                        setSelectedTaskId('__new__');
                        setActive('火花好友');
                      }}
                      className={`rounded-xl border border-dashed px-3 py-2 text-xs ${!primaryTask ? 'border-[#ff7048] bg-[#fff8f5] font-semibold text-[#d95739]' : 'border-black/15 bg-white text-[#777168]'}`}
                    >
                      <Plus className="mr-1 inline size-3.5" />
                      新建计划
                    </button>
                  )}
                </div>
                {primaryTask && (
                  <div className="mt-4 rounded-2xl bg-[#f5f2ec] p-4">
                    <div className="text-[11px] font-bold text-[#726c64]">
                      {primaryTask.name} 的 {plannedFriends.length} 位好友
                    </div>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {plannedFriends.map((friend) => (
                        <span
                          key={friend.id}
                          className="rounded-full bg-white px-3 py-1.5 text-xs text-[#4f4942] shadow-sm"
                        >
                          {friend.contact_name}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </article>
              <div className="grid gap-4 lg:grid-cols-[1.2fr_.8fr]">
                <article className="rounded-[22px] border border-black/[.06] bg-[#fffdf9] p-5 shadow-[0_12px_28px_rgba(35,32,27,.04)]">
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-bold">每日执行时间</h3>
                    {!primaryTask && (
                      <Badge
                        variant="outline"
                        className="border-[#72bd91]/35 bg-[#e9f6ee] text-[#34704d]"
                      >
                        系统推荐
                      </Badge>
                    )}
                  </div>
                  <p className="mt-1 text-[11px] text-[#8e887f]">
                    {primaryTask
                      ? '选择任意分钟，系统按服务器容量排队'
                      : '已根据当前全站负载错峰推荐，可自行修改'}
                  </p>
                  <div className="mt-5 grid gap-3 sm:grid-cols-[180px_1fr]">
                    <Input
                      aria-label="每日执行时间"
                      type="time"
                      value={timeWindow}
                      onChange={(event) => setTimeWindow(event.target.value)}
                      className="h-12 rounded-xl border-[#dfe3e9] bg-white px-4 text-base font-bold"
                    />
                    <div
                      className={`rounded-xl border px-4 py-3 text-xs ${scheduleInfo?.available === false ? 'border-[#ef604d]/25 bg-[#fff0ed] text-[#c84a34]' : scheduleInfo?.level === 'busy' ? 'border-[#e7a742]/25 bg-[#fff7e8] text-[#9b681d]' : 'border-[#72bd91]/25 bg-[#edf8f1] text-[#34704d]'}`}
                    >
                      <div className="font-bold">
                        {scheduleInfo
                          ? {
                              idle: '服务器空闲',
                              light: '负载较轻',
                              busy: '负载较忙',
                              full: '该时段已满',
                            }[scheduleInfo.level]
                          : '正在校验负载…'}
                      </div>
                      <div className="mt-1 opacity-75">
                        {scheduleInfo
                          ? `前后 15 分钟已有 ${scheduleInfo.nearby_tasks}/${scheduleInfo.capacity} 个计划${scheduleInfo.estimated_wait_minutes ? `，预计排队约 ${scheduleInfo.estimated_wait_minutes} 分钟` : ''}`
                          : '请稍候'}
                      </div>
                    </div>
                  </div>
                  <div className="mt-6 flex items-center justify-between rounded-2xl bg-[#f4f0e9] p-4">
                    <div>
                      <div className="text-sm font-semibold">自动值守</div>
                      <div className="mt-1 text-[11px] text-[#837d74]">
                        {primaryTask
                          ? '切换后立即保存到服务器'
                          : '创建计划时保存此设置'}
                      </div>
                    </div>
                    <Switch
                      checked={enabled}
                      onCheckedChange={(value) =>
                        primaryTask ? void toggleTask(value) : setEnabled(value)
                      }
                      className="data-checked:bg-[#ff7048]"
                    />
                  </div>
                  <Button
                    onClick={createTask}
                    disabled={scheduleInfo?.available === false}
                    className="mt-5 h-10 w-full rounded-xl bg-[#282d29] text-white hover:bg-[#3b423d]"
                  >
                    {primaryTask ? '保存计划修改' : '保存并创建计划'}
                  </Button>
                </article>
                <article className="rounded-[22px] bg-[#252a27] p-5 text-white shadow-[0_12px_28px_rgba(22,24,22,.12)]">
                  <CalendarClock className="size-6 text-[#ff805b]" />
                  <p className="mt-8 text-[11px] text-white/45">当前计划</p>
                  <div className="mt-1 text-2xl font-bold">
                    {primaryTask?.send_time || '尚未创建'}
                  </div>
                  <div className="mt-5 space-y-3 text-xs text-white/60">
                    <div className="flex justify-between">
                      <span>计划状态</span>
                      <span className="text-white">
                        {primaryTask
                          ? primaryTask.enabled
                            ? '已启用'
                            : '已暂停'
                          : '无计划'}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span>好友数量</span>
                      <span className="text-white">
                        {plannedFriends.length} 位
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span>最大并发</span>
                      <span className="text-white">1 个账号</span>
                    </div>
                  </div>
                </article>
              </div>
            </div>
          )}

          {active === '运行记录' && (
            <div>
              <div className="mb-6 flex flex-col justify-between gap-2 sm:flex-row sm:items-end">
                <div>
                  <p className="text-xs font-semibold text-[#ff6540]">
                    最近记录
                  </p>
                  <h2 className="mt-2 text-[26px] font-bold tracking-[-.035em]">
                    每一次值守都有记录
                  </h2>
                  <p className="mt-1 text-sm text-[#807b73]">
                    所有时间均按北京时间显示；可查看具体计划、好友结果、失败原因和下次重试。
                  </p>
                </div>
                <span className="text-[11px] text-[#979087]">
                  每 15 秒自动更新
                </span>
              </div>
              <article className="overflow-hidden rounded-[22px] border border-black/[.06] bg-[#fffdf9] shadow-[0_12px_28px_rgba(35,32,27,.04)]">
                <div className="flex items-center justify-between gap-3 border-b border-black/[.055] px-5 py-3">
                  <Button
                    onClick={() =>
                      olderRunDate && setSelectedRunDate(olderRunDate)
                    }
                    disabled={!olderRunDate}
                    variant="outline"
                    size="sm"
                    className="rounded-xl border-black/[.08] bg-white"
                  >
                    上一天
                  </Button>
                  <div className="text-center">
                    <div className="text-sm font-bold">
                      {selectedRunDate || '暂无记录'}
                    </div>
                    <div className="mt-0.5 text-[10px] text-[#8e887f]">
                      北京时间 · 一天一页
                    </div>
                  </div>
                  <Button
                    onClick={() =>
                      newerRunDate && setSelectedRunDate(newerRunDate)
                    }
                    disabled={!newerRunDate}
                    variant="outline"
                    size="sm"
                    className="rounded-xl border-black/[.08] bg-white"
                  >
                    下一天
                  </Button>
                </div>
                {accountRuns.length ? (
                  <div className="divide-y divide-black/[.06]">
                    {accountRuns.map((record) => (
                      <div key={record.id} className="p-5">
                        <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                          <div>
                            <div className="font-bold">{record.task_name}</div>
                            <div className="mt-1 text-[11px] text-[#8e887f]">
                              计划执行：{chinaTime(record.scheduled_for)}
                              （北京时间） · 第 {record.attempt} 次
                            </div>
                          </div>
                          <Badge
                            variant="outline"
                            className={
                              ['success', 'recovered'].includes(record.status)
                                ? 'border-[#72bd91]/35 bg-[#e9f6ee] text-[#34704d]'
                                : record.status === 'failed'
                                  ? 'border-[#ef604d]/25 bg-[#fff0eb] text-[#c84a34]'
                                  : 'border-[#e7a742]/30 bg-[#fff8e9] text-[#94631d]'
                            }
                          >
                            {runStatusText[record.status] || record.status}
                          </Badge>
                        </div>
                        {(record.error_message ||
                          record.error_code ||
                          record.next_retry_at) && (
                          <div className="mt-3 rounded-xl bg-[#fff3ee] p-3 text-xs leading-5 text-[#9b4b2e]">
                            <div>
                              <b>失败原因：</b>
                              {record.error_message ||
                                record.error_code ||
                                '未记录详细原因'}
                            </div>
                            {record.next_retry_at && (
                              <div>
                                <b>下次重试：</b>
                                {chinaTime(record.next_retry_at)}（北京时间）
                              </div>
                            )}
                          </div>
                        )}
                        <div className="mt-3 grid gap-2 sm:grid-cols-2">
                          {record.items.length ? (
                            record.items.map((item, index) => (
                              <div
                                key={`${item.contact_name}:${index}`}
                                className="rounded-xl bg-[#f5f2ec] px-3 py-2.5 text-xs"
                              >
                                <div className="flex items-center justify-between gap-2">
                                  <span className="font-semibold">
                                    {item.contact_name}
                                  </span>
                                  <span
                                    className={
                                      item.status === 'submitted'
                                        ? 'text-[#34704d]'
                                        : 'text-[#a0632c]'
                                    }
                                  >
                                    {runStatusText[item.status] || item.status}
                                  </span>
                                </div>
                                {item.reason && (
                                  <div className="mt-1 text-[11px] leading-4 text-[#8e887f]">
                                    {item.reason}
                                  </div>
                                )}
                              </div>
                            ))
                          ) : (
                            <div className="text-xs text-[#8e887f]">
                              本次没有好友明细
                            </div>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="px-5 py-14 text-center text-sm text-[#8e887f]">
                    当前账号还没有运行记录，创建计划后会在这里显示。
                  </div>
                )}
              </article>
            </div>
          )}

          {active === '邮件通知' && (
            <div>
              <div className="mb-6">
                <p className="text-xs font-semibold text-[#ff6540]">
                  不用每天打开网站
                </p>
                <h2 className="mt-2 text-[26px] font-bold tracking-[-.035em]">
                  异常会主动发到你的邮箱
                </h2>
                <p className="mt-1 text-sm text-[#807b73]">
                  通知邮箱：
                  {user?.email
                    ? `${user.email.slice(0, 1)}***@${user.email.split('@')[1]}`
                    : '已验证邮箱'}{' '}
                  · 邮件仅用于任务和账号安全提醒。
                </p>
              </div>
              <div className="grid gap-4 lg:grid-cols-[1.15fr_.85fr]">
                <article className="rounded-[22px] border border-black/[.06] bg-[#fffdf9] p-5 shadow-[0_12px_28px_rgba(35,32,27,.04)]">
                  <div className="flex items-start justify-between border-b border-black/[.055] pb-5">
                    <div>
                      <h3 className="text-sm font-bold">邮件提醒</h3>
                      <p className="mt-1 text-[11px] text-[#8e887f]">
                        关闭后将不再发送任何任务邮件
                      </p>
                    </div>
                    <Switch
                      checked={emailEnabled}
                      onCheckedChange={(value) =>
                        savePreferences({ ...prefs, enabled: value })
                      }
                      className="data-checked:bg-[#ff7048]"
                    />
                  </div>
                  <div
                    className={`divide-y divide-black/[.05] transition ${emailEnabled ? '' : 'pointer-events-none opacity-40'}`}
                  >
                    {(
                      [
                        [
                          'task_failed',
                          '连续失败 3 次',
                          '自动重试仍未完成时立即通知',
                        ],
                        [
                          'login_expired',
                          '账号登录失效',
                          '需要重新扫码登录时立即通知',
                        ],
                        [
                          'security_challenge',
                          '安全验证或风控',
                          '出现验证码、访问受限等情况时通知',
                        ],
                        [
                          'daily_incomplete',
                          '当天任务未完成',
                          '最晚 23:20 仍失败时发送紧急提醒',
                        ],
                        [
                          'daily_summary',
                          '每日成功摘要',
                          '每天任务完成后发送一封汇总邮件',
                        ],
                      ] as const
                    ).map(([key, title, desc]) => (
                      <div
                        key={key}
                        className="flex items-center justify-between gap-5 py-4"
                      >
                        <div>
                          <div className="text-sm font-semibold">{title}</div>
                          <div className="mt-1 text-[11px] text-[#8e887f]">
                            {desc}
                          </div>
                        </div>
                        <Switch
                          checked={prefs[key]}
                          onCheckedChange={(value) =>
                            savePreferences({ ...prefs, [key]: value })
                          }
                          className="data-checked:bg-[#ff7048]"
                        />
                      </div>
                    ))}
                  </div>
                  <Button
                    onClick={sendTestEmail}
                    variant="outline"
                    className="mt-3 h-10 w-full rounded-xl border-black/[.08] bg-white"
                  >
                    <Mail className="size-4" />
                    发送测试邮件
                  </Button>
                </article>
                <div className="space-y-4">
                  <article className="rounded-[22px] bg-[#252a27] p-5 text-white shadow-[0_12px_28px_rgba(22,24,22,.12)]">
                    <div className="grid size-10 place-items-center rounded-xl bg-[#ff7048]">
                      <Mail className="size-[18px]" />
                    </div>
                    <h3 className="mt-6 text-lg font-bold">通知服务状态</h3>
                    <p className="mt-2 text-xs leading-5 text-white/50">
                      所有提醒都发送到当前已验证邮箱。临时网络问题会先重试，只有需要处理时才通知你。
                    </p>
                    <div className="mt-5 rounded-xl bg-white/[.07] p-3 text-[11px] text-white/55">
                      <div className="flex justify-between">
                        <span>总开关</span>
                        <span className="text-white">
                          {emailEnabled ? '已开启' : '已关闭'}
                        </span>
                      </div>
                      <div className="mt-2 flex justify-between">
                        <span>最近失败任务</span>
                        <span
                          className={
                            failedRuns ? 'text-[#ff9a7c]' : 'text-[#79d19c]'
                          }
                        >
                          {failedRuns} 个
                        </span>
                      </div>
                    </div>
                  </article>
                  <article className="rounded-[22px] border border-black/[.06] bg-[#fffdf9] p-5">
                    <h3 className="text-sm font-bold">通知原则</h3>
                    <p className="mt-2 text-[11px] leading-5 text-[#857f76]">
                      临时网络错误会先自动重试，不会每次都发邮件。只有需要你处理或当天任务最终未完成时才打扰你。
                    </p>
                  </article>
                </div>
              </div>
            </div>
          )}

          {active === '升级 Pro' && (
            <div>
              <div className="mb-6">
                <div className="flex items-center gap-2 text-xs font-semibold text-[#ff6540]">
                  <Crown className="size-3.5" />
                  使用额度
                </div>
                <h2 className="mt-2 text-[26px] font-bold tracking-[-.035em] md:text-[30px]">
                  让更多火花持续在线
                </h2>
                <p className="mt-1 text-sm text-[#807b73]">
                  自行部署的管理员默认拥有完整权益，其他用户的额度由本部署管理员管理。
                </p>
              </div>

              {user?.is_admin && (
                <div className="mb-5 flex items-center gap-3 rounded-2xl border border-[#72bd91]/30 bg-[#edf8f1] px-4 py-3 text-sm text-[#34704d]">
                  <ShieldCheck className="size-4 shrink-0" />
                  <div>
                    <b>管理员账号</b>
                    <span className="ml-2 text-xs">
                      默认拥有 Pro 同等权益，不受会员有效期限制
                    </span>
                  </div>
                </div>
              )}
              {user?.plan_tier === 'pro' && !user.is_admin && (
                <div className="mb-5 flex items-center gap-3 rounded-2xl border border-[#72bd91]/30 bg-[#edf8f1] px-4 py-3 text-sm text-[#34704d]">
                  <Check className="size-4 shrink-0" />
                  <div>
                    <b>
                      你当前是 Pro {proPeriodText[user.plan_period || 'year']}
                    </b>
                    {planExpiryLabel && (
                      <span className="ml-2 text-xs">
                        有效期至 {planExpiryLabel}
                      </span>
                    )}
                  </div>
                </div>
              )}

              <div className="grid gap-4 xl:grid-cols-[1.35fr_.65fr]">
                <div className="space-y-4">
                  <div className="grid gap-4 md:grid-cols-2">
                    <article className="rounded-[24px] border border-black/[.07] bg-[#fffdf9] p-6 shadow-[0_12px_28px_rgba(35,32,27,.04)]">
                      <div className="flex items-start justify-between">
                        <div>
                          <div className="text-xs font-bold text-[#7f786f]">
                            体验版
                          </div>
                          <div className="mt-3 text-3xl font-bold">7 天</div>
                        </div>
                        <Badge
                          variant="outline"
                          className="border-black/10 bg-[#f2efe9] text-[#736d65]"
                        >
                          注册即享
                        </Badge>
                      </div>
                      <div className="mt-6 space-y-3 text-sm text-[#625c55]">
                        <div className="flex items-center gap-2">
                          <Check className="size-4 text-[#6ca37e]" />
                          最多 5 个执行计划
                        </div>
                        <div className="flex items-center gap-2">
                          <Check className="size-4 text-[#6ca37e]" />
                          所有计划合计最多 10 人
                        </div>
                        <div className="flex items-center gap-2">
                          <Check className="size-4 text-[#6ca37e]" />
                          每个计划最多 5 人
                        </div>
                        <div className="flex items-center gap-2">
                          <Check className="size-4 text-[#6ca37e]" />
                          最多绑定 5 个抖音账号
                        </div>
                      </div>
                    </article>
                    <article className="relative overflow-hidden rounded-[24px] border border-[#ff7048]/25 bg-[#fff5f1] p-6 shadow-[0_18px_40px_rgba(211,79,42,.10)]">
                      <div className="absolute -right-12 -top-14 size-40 rounded-full bg-[#ff7048]/[.07]" />
                      <div className="relative flex items-start justify-between">
                        <div>
                          <div className="flex items-center gap-2 text-xs font-bold text-[#d95739]">
                            <Crown className="size-4" />
                            扩展额度
                          </div>
                          <div className="mt-3 text-2xl font-bold">
                            由本部署管理员开通
                          </div>
                        </div>
                        <Badge className="border-0 bg-[#ff7048] text-white">
                          自托管
                        </Badge>
                      </div>
                      <div className="relative mt-6 space-y-3 text-sm text-[#5d514c]">
                        <div className="flex items-center gap-2">
                          <Check className="size-4 text-[#e65d38]" />
                          最多 10 个执行计划
                        </div>
                        <div className="flex items-center gap-2">
                          <Check className="size-4 text-[#e65d38]" />
                          所有计划合计最多 25 人
                        </div>
                        <div className="flex items-center gap-2">
                          <Check className="size-4 text-[#e65d38]" />
                          每个计划最多 5 人
                        </div>
                        <div className="flex items-center gap-2">
                          <Check className="size-4 text-[#e65d38]" />
                          最多绑定 5 个抖音账号
                        </div>
                      </div>
                    </article>
                  </div>

                  <article className="overflow-hidden rounded-[24px] border border-black/[.06] bg-[#fffdf9] shadow-[0_12px_28px_rgba(35,32,27,.04)]">
                    <div className="border-b border-black/[.055] px-5 py-4">
                      <h3 className="text-sm font-bold">权益对比</h3>
                      <p className="mt-1 text-[11px] text-[#8e887f]">
                        计划和人数上限按同一个网站账号下的所有抖音号合计。
                      </p>
                    </div>
                    <div className="grid grid-cols-[1.2fr_.8fr_.8fr] text-sm">
                      <div className="bg-[#f5f2ec] px-4 py-3 text-xs font-bold text-[#756f67]">
                        权益
                      </div>
                      <div className="bg-[#f5f2ec] px-4 py-3 text-center text-xs font-bold text-[#756f67]">
                        体验版
                      </div>
                      <div className="bg-[#fff0eb] px-4 py-3 text-center text-xs font-bold text-[#d95739]">
                        Pro 版
                      </div>
                      {[
                        ['有效期', '7 天', '7 / 30 / 90 / 365 天'],
                        ['执行计划总数', '5 个', '10 个'],
                        ['计划总人数', '10 人', '25 人'],
                        ['每个计划人数', '5 人', '5 人'],
                        ['抖音账号数量', '5 个', '5 个'],
                      ].map(([label, trial, pro]) => (
                        <div key={label} className="contents">
                          <div className="border-t border-black/[.055] px-4 py-3 text-[#5f5952]">
                            {label}
                          </div>
                          <div className="border-t border-black/[.055] px-4 py-3 text-center text-[#817a72]">
                            {trial}
                          </div>
                          <div className="border-t border-[#ff7048]/10 bg-[#fffaf8] px-4 py-3 text-center font-semibold text-[#c85132]">
                            {pro}
                          </div>
                        </div>
                      ))}
                    </div>
                  </article>
                </div>

                <article className="self-start rounded-[24px] border border-black/[.06] bg-[#fffdf9] p-5 text-center shadow-[0_12px_28px_rgba(35,32,27,.04)] xl:sticky xl:top-24">
                  <div className="mx-auto grid size-11 place-items-center rounded-2xl bg-[#fff0eb] text-[#d95739]">
                    <ShieldCheck className="size-5" />
                  </div>
                  <h3 className="mt-4 text-lg font-bold">由部署管理员管理</h3>
                  <p className="mt-2 text-xs leading-5 text-[#8e887f]">
                    本社区版不包含支付或官方销售渠道。需要扩展额度时，请联系你正在使用的这套实例的管理员。
                  </p>
                  <div className="mt-5 rounded-2xl bg-[#f4f0e9] p-4 text-left text-xs leading-5 text-[#6f6961]">
                    如果这是你自己部署的实例，请使用配置中的管理员邮箱注册。管理员账号默认不受会员有效期限制，并可在“我的管理”中管理其他用户。
                  </div>
                </article>
              </div>
            </div>
          )}

          {active === '我的管理' && user?.is_admin && (
            <div>
              <div className="mb-6 flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
                <div>
                  <p className="text-xs font-semibold text-[#ff6540]">
                    仅管理员可见
                  </p>
                  <h2 className="mt-2 text-[26px] font-bold tracking-[-.035em]">
                    用户诊断与邀请码
                  </h2>
                  <p className="mt-1 text-sm text-[#807b73]">
                    查看账号、额度、绑定和任务故障；敏感凭据不会在后台显示。
                  </p>
                </div>
                <span className="text-[11px] text-[#979087]">
                  每 15 秒自动更新
                </span>
              </div>

              <article className="mb-4 overflow-hidden rounded-[22px] border border-black/[.06] bg-[#fffdf9] shadow-[0_12px_28px_rgba(35,32,27,.04)]">
                <div className="border-b border-black/[.055] px-5 py-4">
                  <h3 className="text-sm font-bold">网站用户与抖音账号</h3>
                  <p className="mt-1 text-[11px] text-[#8e887f]">
                    可排查绑定状态、最近错误和运行失败；不会展示密码、Cookie
                    或验证码。
                  </p>
                </div>
                {adminUsers.length ? (
                  <div className="divide-y divide-black/[.06]">
                    {adminUsers.map((managedUser) => {
                      const expanded = expandedAdminUserId === managedUser.id;
                      return (
                        <div key={managedUser.id} className="px-5 py-4">
                          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                            <button
                              type="button"
                              onClick={() =>
                                setExpandedAdminUserId(
                                  expanded ? '' : managedUser.id,
                                )
                              }
                              className="min-w-0 flex-1 text-left"
                            >
                              <div className="flex flex-wrap items-center gap-2">
                                <span className="font-bold">
                                  {managedUser.username}
                                </span>
                                {managedUser.is_admin && (
                                  <Badge
                                    variant="outline"
                                    className="border-[#ff7048]/30 bg-[#fff1ec] text-[#d95739]"
                                  >
                                    管理员
                                  </Badge>
                                )}
                                <Badge
                                  variant="outline"
                                  className={
                                    managedUser.plan_tier === 'expired'
                                      ? 'border-black/10 bg-[#f0eee9] text-[#817b72]'
                                      : 'border-[#72bd91]/35 bg-[#e9f6ee] text-[#34704d]'
                                  }
                                >
                                  {managedUser.is_admin
                                    ? '高级权限'
                                    : managedUser.plan_tier === 'pro'
                                      ? `Pro ${proPeriodText[managedUser.plan_period || 'year']}`
                                      : managedUser.plan_tier === 'expired'
                                        ? `${managedUser.plan_period ? `Pro ${proPeriodText[managedUser.plan_period]}` : '体验版'}已到期`
                                        : `体验版剩 ${managedUser.plan_remaining_days ?? '—'} 天`}
                                </Badge>
                              </div>
                              <div className="mt-1 truncate text-[11px] text-[#8e887f]">
                                {managedUser.email} · 注册于{' '}
                                {chinaTime(managedUser.created_at)} ·{' '}
                                {managedUser.accounts.length} 个抖音账号
                              </div>
                            </button>
                            <div className="flex shrink-0 items-center gap-2">
                              {!managedUser.is_admin &&
                                managedUser.plan_tier !== 'pro' && (
                                  <select
                                    aria-label={`选择${managedUser.username}的 Pro 卡种`}
                                    value={
                                      upgradePeriods[managedUser.id] || 'year'
                                    }
                                    onChange={(event) =>
                                      setUpgradePeriods((current) => ({
                                        ...current,
                                        [managedUser.id]: event.target
                                          .value as ProPeriod,
                                      }))
                                    }
                                    className="h-8 rounded-xl border border-black/[.08] bg-white px-2 text-xs"
                                  >
                                    <option value="week">周卡</option>
                                    <option value="month">月卡</option>
                                    <option value="quarter">季卡</option>
                                    <option value="year">年卡</option>
                                  </select>
                                )}
                              {!managedUser.is_admin && (
                                <Button
                                  onClick={() =>
                                    void upgradeUserToPro(managedUser)
                                  }
                                  disabled={
                                    Boolean(upgradingUserId) ||
                                    managedUser.plan_tier === 'pro'
                                  }
                                  size="sm"
                                  className={
                                    managedUser.plan_tier === 'pro'
                                      ? 'h-8 rounded-xl bg-[#d8d5cf] px-3 text-[#8a857d] disabled:opacity-100'
                                      : 'h-8 rounded-xl bg-[#ff7048] px-3 text-white hover:bg-[#ef603a]'
                                  }
                                >
                                  <Crown className="size-3.5" />
                                  {managedUser.plan_tier === 'pro'
                                    ? `已是 Pro ${proPeriodText[managedUser.plan_period || 'year']}`
                                    : upgradingUserId === managedUser.id
                                      ? '开通中…'
                                      : '一键开通 Pro'}
                                </Button>
                              )}
                              <Button
                                type="button"
                                onClick={() =>
                                  setExpandedAdminUserId(
                                    expanded ? '' : managedUser.id,
                                  )
                                }
                                variant="outline"
                                size="sm"
                                className="h-8 rounded-xl border-black/[.08] bg-white"
                              >
                                {expanded ? (
                                  <ChevronUp className="size-3.5" />
                                ) : (
                                  <ChevronDown className="size-3.5" />
                                )}
                                {expanded ? '收起' : '查看详情'}
                              </Button>
                            </div>
                          </div>
                          {expanded && (
                            <div className="mt-4 overflow-hidden rounded-2xl border border-black/[.07] bg-white">
                              <div className="flex gap-1 border-b border-black/[.06] bg-[#f5f2ec] p-1.5">
                                <button
                                  type="button"
                                  onClick={() => setAdminDetailMode('plans')}
                                  className={`flex-1 rounded-xl px-3 py-2 text-xs font-semibold transition ${adminDetailMode === 'plans' ? 'bg-white text-[#d95739] shadow-sm' : 'text-[#777067]'}`}
                                >
                                  执行计划
                                </button>
                                <button
                                  type="button"
                                  onClick={() => setAdminDetailMode('runs')}
                                  className={`flex-1 rounded-xl px-3 py-2 text-xs font-semibold transition ${adminDetailMode === 'runs' ? 'bg-white text-[#d95739] shadow-sm' : 'text-[#777067]'}`}
                                >
                                  运行记录
                                </button>
                              </div>
                              {!managedUser.accounts.length ? (
                                <div className="px-4 py-8 text-center text-xs text-[#8e887f]">
                                  该用户还没有添加抖音账号
                                </div>
                              ) : (
                                <div className="divide-y divide-black/[.055]">
                                  {managedUser.accounts.map((account) => {
                                    const activeBinding =
                                      account.latest_binding &&
                                      [
                                        'pending',
                                        'running',
                                        'qr_ready',
                                        'verification_required',
                                        'verification_submitted',
                                      ].includes(account.latest_binding.status);
                                    return (
                                      <div key={account.id} className="p-4">
                                        <div className="flex flex-wrap items-center justify-between gap-2">
                                          <div>
                                            <span className="font-semibold">
                                              {account.name}
                                            </span>
                                            <span className="ml-2 text-[11px] text-[#8e887f]">
                                              {statusText[account.status] ||
                                                account.status}{' '}
                                              · 计划 {account.enabled_tasks}/
                                              {account.tasks}
                                            </span>
                                          </div>
                                          {activeBinding &&
                                            account.latest_binding && (
                                              <Button
                                                onClick={() =>
                                                  clearStuckBinding(
                                                    account.latest_binding!.id,
                                                  )
                                                }
                                                variant="outline"
                                                size="sm"
                                                className="h-8 rounded-xl border-[#ef604d]/20 bg-white text-[#d95739]"
                                              >
                                                <Ban className="size-3.5" />
                                                清理卡住的绑定
                                              </Button>
                                            )}
                                        </div>
                                        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 rounded-xl bg-[#faf8f4] px-3 py-2 text-[10px] leading-4 text-[#7d766d]">
                                          <span>
                                            绑定时间：
                                            {account.bound_at
                                              ? chinaTime(account.bound_at)
                                              : '尚未完成绑定'}
                                          </span>
                                          <span>
                                            最近绑定：
                                            {account.latest_binding
                                              ? bindingStatusText[
                                                  account.latest_binding.status
                                                ] ||
                                                account.latest_binding.status
                                              : '无记录'}
                                          </span>
                                          {account.latest_binding
                                            ?.error_message &&
                                            ['error', 'expired'].includes(
                                              account.latest_binding.status,
                                            ) && (
                                              <span className="text-[#b25035]">
                                                绑定异常：
                                                {
                                                  account.latest_binding
                                                    .error_message
                                                }
                                              </span>
                                            )}
                                        </div>
                                        {adminDetailMode === 'plans' ? (
                                          <div className="mt-3 grid gap-2 md:grid-cols-2">
                                            {account.plans.length ? (
                                              account.plans.map((plan) => (
                                                <div
                                                  key={plan.id}
                                                  className="rounded-xl bg-[#f5f2ec] p-3"
                                                >
                                                  <div className="flex items-center justify-between gap-3 text-xs">
                                                    <b>{plan.name}</b>
                                                    <span
                                                      className={
                                                        plan.enabled
                                                          ? 'text-[#34704d]'
                                                          : 'text-[#8e887f]'
                                                      }
                                                    >
                                                      {plan.enabled
                                                        ? '已启用'
                                                        : '已停用'}
                                                    </span>
                                                  </div>
                                                  <div className="mt-1 text-[11px] text-[#756f67]">
                                                    每日 {plan.send_time}
                                                    （北京时间） ·{' '}
                                                    {
                                                      plan.recipients.length
                                                    }{' '}
                                                    位好友
                                                  </div>
                                                  <div className="mt-2 flex flex-wrap gap-1.5">
                                                    {plan.recipients.length ? (
                                                      plan.recipients.map(
                                                        (name, index) => (
                                                          <span
                                                            key={`${plan.id}:${index}`}
                                                            className="rounded-lg bg-white px-2 py-1 text-[10px] text-[#686159]"
                                                          >
                                                            {name}
                                                          </span>
                                                        ),
                                                      )
                                                    ) : (
                                                      <span className="text-[10px] text-[#9a938a]">
                                                        未设置好友
                                                      </span>
                                                    )}
                                                  </div>
                                                </div>
                                              ))
                                            ) : (
                                              <div className="text-xs text-[#8e887f]">
                                                该账号暂无执行计划
                                              </div>
                                            )}
                                          </div>
                                        ) : (
                                          <AdminAccountRuns
                                            accountId={account.id}
                                          />
                                        )}
                                      </div>
                                    );
                                  })}
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="px-5 py-14 text-center text-sm text-[#8e887f]">
                    {adminLoading ? '正在读取用户信息…' : '还没有用户记录'}
                  </div>
                )}
              </article>

              <div className="grid gap-4 xl:grid-cols-[.8fr_1.2fr]">
                <article className="rounded-[22px] border border-black/[.06] bg-[#fffdf9] p-5 shadow-[0_12px_28px_rgba(35,32,27,.04)]">
                  <div className="flex items-center gap-3">
                    <div className="grid size-10 place-items-center rounded-xl bg-[#ff7048] text-white">
                      <Plus className="size-5" />
                    </div>
                    <div>
                      <h3 className="text-sm font-bold">创建邀请码</h3>
                      <p className="mt-1 text-[11px] text-[#8e887f]">
                        完整邀请码只显示这一次
                      </p>
                    </div>
                  </div>
                  <div className="mt-6 grid grid-cols-2 gap-3">
                    <div>
                      <Label
                        htmlFor="invite-uses"
                        className="text-xs font-bold"
                      >
                        可注册人数
                      </Label>
                      <Input
                        id="invite-uses"
                        type="number"
                        min={1}
                        max={100}
                        value={inviteUses}
                        onChange={(event) =>
                          setInviteUses(
                            Math.min(
                              100,
                              Math.max(1, Number(event.target.value) || 1),
                            ),
                          )
                        }
                        className="mt-2 h-11 rounded-xl border-[#dfe3e9]"
                      />
                    </div>
                    <div>
                      <Label
                        htmlFor="invite-days"
                        className="text-xs font-bold"
                      >
                        有效天数
                      </Label>
                      <Input
                        id="invite-days"
                        type="number"
                        min={1}
                        max={365}
                        value={inviteDays}
                        onChange={(event) =>
                          setInviteDays(
                            Math.min(
                              365,
                              Math.max(1, Number(event.target.value) || 1),
                            ),
                          )
                        }
                        className="mt-2 h-11 rounded-xl border-[#dfe3e9]"
                      />
                    </div>
                  </div>
                  <Button
                    onClick={createInvite}
                    disabled={inviteLoading}
                    className="mt-5 h-11 w-full rounded-xl bg-[#282d29] text-white hover:bg-[#3b423d]"
                  >
                    <TicketCheck className="size-4" />
                    {inviteLoading ? '正在生成…' : '生成新邀请码'}
                  </Button>
                  {generatedInvite && (
                    <div className="mt-4 rounded-2xl border border-[#ff7048]/20 bg-[#fff1ec] p-4">
                      <p className="text-[10px] font-semibold text-[#d65b38]">
                        这是本次新生成的邀请码，请现在复制保存；再次生成时这里会立即清空
                      </p>
                      <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center">
                        <code className="min-w-0 flex-1 select-all overflow-x-auto rounded-xl bg-white px-3 py-3 text-center text-sm font-bold tracking-[.08em] text-[#292622]">
                          {generatedInvite}
                        </code>
                        <Button
                          onClick={copyInvite}
                          disabled={inviteLoading}
                          className="h-11 shrink-0 rounded-xl bg-[#ff7048] px-4 text-white hover:bg-[#ef603a]"
                          aria-label="复制本次新生成的邀请码"
                        >
                          <Copy className="size-4" />
                          复制本次邀请码
                        </Button>
                      </div>
                    </div>
                  )}
                  <div className="mt-4 flex items-start gap-2 rounded-xl bg-[#f4f0e9] p-3 text-[11px] leading-5 text-[#7d766d]">
                    <ShieldCheck className="mt-0.5 size-4 shrink-0 text-[#4f8a65]" />
                    服务器只保存邀请码摘要，任何人都无法从数据库还原完整代码。
                  </div>
                </article>

                <article className="overflow-hidden rounded-[22px] border border-black/[.06] bg-[#fffdf9] shadow-[0_12px_28px_rgba(35,32,27,.04)]">
                  <div className="border-b border-black/[.055] px-5 py-4">
                    <h3 className="text-sm font-bold">邀请码记录</h3>
                    <p className="mt-1 text-[11px] text-[#8e887f]">
                      下面只显示记录编号，不能用于注册；完整邀请码只在生成时显示一次。
                    </p>
                  </div>
                  {invites.length ? (
                    <div className="divide-y divide-black/[.05]">
                      {invites.map((invite) => {
                        const unavailable =
                          !invite.active ||
                          invite.expired ||
                          invite.uses >= invite.max_uses;
                        return (
                          <div
                            key={invite.id}
                            className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center"
                          >
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-2">
                                <span className="font-mono text-sm font-bold">
                                  记录编号 ·{' '}
                                  {invite.id.slice(0, 8).toUpperCase()}
                                </span>
                                <Badge
                                  variant="outline"
                                  className={
                                    unavailable
                                      ? 'border-black/10 bg-[#f0eee9] text-[#817b72]'
                                      : 'border-[#72bd91]/35 bg-[#e9f6ee] text-[#34704d]'
                                  }
                                >
                                  {invite.expired
                                    ? '已过期'
                                    : invite.uses >= invite.max_uses
                                      ? '已用完'
                                      : invite.active
                                        ? '可使用'
                                        : '已停用'}
                                </Badge>
                              </div>
                              <div className="mt-1 text-[11px] text-[#8e887f]">
                                该编号不能注册 · 已用 {invite.uses}/
                                {invite.max_uses} ·{' '}
                                {invite.expires_at
                                  ? `${chinaTime(invite.expires_at, true)} 到期`
                                  : '长期有效'}
                              </div>
                            </div>
                            <div className="flex gap-2">
                              <Button
                                onClick={() =>
                                  setInviteActive(invite, !invite.active)
                                }
                                variant="outline"
                                size="sm"
                                className="rounded-xl border-black/[.08] bg-white"
                              >
                                {invite.active ? (
                                  <>
                                    <Ban className="size-3.5" />
                                    停用
                                  </>
                                ) : (
                                  <>
                                    <Check className="size-3.5" />
                                    启用
                                  </>
                                )}
                              </Button>
                              {invite.uses === 0 && (
                                <Button
                                  onClick={() => removeInvite(invite)}
                                  variant="outline"
                                  size="icon"
                                  className="size-8 rounded-xl border-[#ef604d]/20 text-[#d95739]"
                                  aria-label="删除邀请码"
                                >
                                  <Trash2 className="size-3.5" />
                                </Button>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="px-5 py-16 text-center text-sm text-[#8e887f]">
                      还没有邀请码记录，先创建一个发给朋友。
                    </div>
                  )}
                </article>
              </div>
            </div>
          )}
        </div>
        <SiteFooter />
      </section>

      <nav className="fixed inset-x-3 bottom-[calc(.75rem+env(safe-area-inset-bottom))] z-40 flex h-16 items-center justify-around rounded-[20px] border border-white/10 bg-[#202522]/95 px-2 text-white shadow-2xl backdrop-blur lg:hidden">
        {mobilePrimaryNav.map((item) => {
          const Icon = item.icon;
          const selected = active === item.label;
          return (
            <button
              key={item.label}
              onClick={() => setActive(item.label)}
              className={`flex min-w-12 flex-1 flex-col items-center gap-1 text-[10px] ${selected ? 'text-[#ff825d]' : 'text-white/48'}`}
            >
              <Icon className="size-[18px]" />
              {item.label === '账号绑定'
                ? '账号'
                : item.label === '火花好友'
                  ? '好友'
                  : item.label === '执行计划'
                    ? '计划'
                    : item.label}
            </button>
          );
        })}
        <button
          onClick={() => setMobileMenuOpen(true)}
          className={`flex min-w-12 flex-1 flex-col items-center gap-1 text-[10px] ${!mobilePrimaryNav.some((item) => item.label === active) ? 'text-[#ff825d]' : 'text-white/48'}`}
        >
          <Menu className="size-[18px]" />
          更多
        </button>
      </nav>

      {mobileMenuOpen && (
        <div className="fixed inset-0 z-50 bg-black/35 backdrop-blur-[2px] lg:hidden">
          <button
            type="button"
            className="absolute inset-0 size-full cursor-default"
            onClick={() => setMobileMenuOpen(false)}
            aria-label="关闭更多菜单"
          />
          <div className="absolute inset-x-0 bottom-0 rounded-t-[28px] bg-[#fffdf9] px-5 pb-[calc(1.25rem+env(safe-area-inset-bottom))] pt-4 shadow-2xl">
            <div className="mx-auto mb-4 h-1 w-10 rounded-full bg-black/10" />
            <div className="mb-4 flex items-center gap-3">
              <Avatar className="size-10 after:border-black/[.05]">
                <AvatarFallback className="bg-[#282d29] text-sm text-white">
                  {user?.username?.slice(0, 1).toUpperCase() || 'V'}
                </AvatarFallback>
              </Avatar>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-bold">
                  {user?.username || '用户'}
                </div>
                <div className="truncate text-[11px] text-[#8e887f]">
                  {user?.is_admin
                    ? '管理员权限'
                    : user?.plan_tier === 'pro'
                      ? `Pro ${proPeriodText[user.plan_period || 'year']} · ${planExpiryLabel || '有效期内'}`
                      : user?.plan_tier === 'expired'
                        ? `${expiredPlanName(user)}已于 ${planExpiryLabel || '—'} 到期`
                        : `体验版 · 剩 ${user?.plan_remaining_days ?? '—'} 天 · ${planExpiryLabel || '—'} 到期`}
                </div>
              </div>
              <button
                onClick={() => setMobileMenuOpen(false)}
                className="grid size-9 place-items-center rounded-xl bg-[#f3efe8]"
                aria-label="关闭菜单"
              >
                <X className="size-4" />
              </button>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {visibleNav.map((item) => {
                const Icon = item.icon;
                const selected = active === item.label;
                return (
                  <button
                    key={item.label}
                    onClick={() => {
                      setActive(item.label);
                      setMobileMenuOpen(false);
                    }}
                    className={`flex h-12 items-center gap-3 rounded-2xl px-4 text-sm ${selected ? 'bg-[#fff0eb] font-semibold text-[#d95739]' : 'bg-[#f5f1ea] text-[#5f5a53]'}`}
                  >
                    <Icon className="size-[18px]" />
                    {item.label}
                  </button>
                );
              })}
            </div>
            <Button
              onClick={() => {
                setMobileMenuOpen(false);
                setChangePasswordOpen(true);
              }}
              variant="outline"
              className="mt-3 h-11 w-full rounded-2xl border-black/[.08] bg-white text-[#4e4943]"
            >
              <KeyRound className="size-4" />
              修改密码
            </Button>
            <Button
              onClick={() => {
                setMobileMenuOpen(false);
                void logout();
              }}
              variant="outline"
              className="mt-2 h-11 w-full rounded-2xl border-black/[.08] bg-white text-[#6e675f]"
            >
              <LogOut className="size-4" />
              退出登录
            </Button>
          </div>
        </div>
      )}

      {changePasswordOpen && (
        <div className="fixed inset-0 z-[60] grid place-items-end bg-black/40 p-0 backdrop-blur-[2px] sm:place-items-center sm:p-5">
          <button
            type="button"
            className="absolute inset-0 size-full cursor-default"
            onClick={() =>
              !changePasswordLoading && setChangePasswordOpen(false)
            }
            aria-label="关闭修改密码窗口"
          />
          <form
            onSubmit={handleChangePassword}
            className="relative w-full rounded-t-[28px] bg-[#fffdf9] p-6 shadow-2xl sm:max-w-md sm:rounded-[24px]"
          >
            <div className="flex items-start justify-between">
              <div>
                <div className="grid size-11 place-items-center rounded-2xl bg-[#fff0eb] text-[#d95739]">
                  <KeyRound className="size-5" />
                </div>
                <h2 className="mt-5 text-xl font-bold">修改登录密码</h2>
                <p className="mt-1 text-xs text-[#8e887f]">
                  修改后其他设备上的登录会自动退出。
                </p>
              </div>
              <button
                type="button"
                onClick={() => setChangePasswordOpen(false)}
                disabled={changePasswordLoading}
                className="grid size-9 place-items-center rounded-xl bg-[#f3efe8]"
                aria-label="关闭"
              >
                <X className="size-4" />
              </button>
            </div>
            <div className="mt-6 space-y-4">
              <div className="space-y-2">
                <Label htmlFor="current-password" className="text-xs font-bold">
                  当前密码
                </Label>
                <PasswordInput
                  id="current-password"
                  name="currentPassword"
                  visible={showCurrentPassword}
                  onToggle={() => setShowCurrentPassword((value) => !value)}
                  onChange={() => setChangePasswordError('')}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="changed-password" className="text-xs font-bold">
                  新密码
                </Label>
                <PasswordInput
                  id="changed-password"
                  name="changedPassword"
                  visible={showChangedPassword}
                  onToggle={() => setShowChangedPassword((value) => !value)}
                  minLength={8}
                  onChange={() => setChangePasswordError('')}
                />
              </div>
              <div className="space-y-2">
                <Label
                  htmlFor="changed-password-confirm"
                  className="text-xs font-bold"
                >
                  确认新密码
                </Label>
                <PasswordInput
                  id="changed-password-confirm"
                  name="changedPasswordConfirm"
                  visible={showChangedPasswordConfirm}
                  onToggle={() =>
                    setShowChangedPasswordConfirm((value) => !value)
                  }
                  minLength={8}
                  onChange={() => setChangePasswordError('')}
                />
              </div>
              {changePasswordError && (
                <div
                  role="alert"
                  className="rounded-xl border border-[#ff6854]/20 bg-[#fff0ed] px-3 py-2.5 text-xs font-semibold text-[#c83b2d]"
                >
                  {changePasswordError}
                </div>
              )}
            </div>
            <Button
              type="submit"
              disabled={changePasswordLoading}
              className="mt-6 h-11 w-full rounded-xl bg-[#282d29] text-white hover:bg-[#3b423d]"
            >
              {changePasswordLoading ? '修改中…' : '确认修改密码'}
            </Button>
          </form>
        </div>
      )}

      {accountPendingDelete && (
        <div className="fixed inset-0 z-[60] grid place-items-end bg-black/40 p-0 backdrop-blur-[2px] sm:place-items-center sm:p-5">
          <button
            type="button"
            className="absolute inset-0 size-full cursor-default"
            onClick={() => !deletingAccount && setAccountPendingDelete(null)}
            aria-label="关闭删除账号窗口"
          />
          <dialog
            open
            aria-labelledby="delete-account-title"
            className="relative m-0 w-full rounded-t-[28px] border-0 bg-[#fffdf9] p-6 text-inherit shadow-2xl sm:max-w-md sm:rounded-[24px]"
          >
            <div className="grid size-11 place-items-center rounded-2xl bg-[#fff0eb] text-[#d95739]">
              <Trash2 className="size-5" />
            </div>
            <h2 id="delete-account-title" className="mt-5 text-xl font-bold">
              删除“{accountPendingDelete.name}”？
            </h2>
            <p className="mt-2 text-sm leading-6 text-[#7f786f]">
              此操作会一并删除该抖音账号保存的登录状态、执行计划、好友配置和运行记录，且无法恢复。
            </p>
            <div className="mt-6 flex gap-2">
              <Button
                onClick={() => setAccountPendingDelete(null)}
                disabled={deletingAccount}
                variant="outline"
                className="h-11 flex-1 rounded-xl border-black/[.08] bg-white"
              >
                取消
              </Button>
              <Button
                onClick={removeAccount}
                disabled={deletingAccount}
                className="h-11 flex-1 rounded-xl bg-[#d95739] text-white hover:bg-[#c84b30]"
              >
                {deletingAccount ? '删除中…' : '确认删除'}
              </Button>
            </div>
          </dialog>
        </div>
      )}

      {notice && (
        <div className="toast fixed bottom-24 left-1/2 z-50 flex -translate-x-1/2 items-center gap-2 rounded-full bg-[#222724] px-4 py-2.5 text-xs font-medium text-white shadow-xl lg:bottom-7">
          <Check className="size-4 text-[#79d19c]" />
          {notice}
          <button onClick={() => setNotice('')} className="ml-1 text-white/45">
            <X className="size-3.5" />
          </button>
        </div>
      )}
    </main>
  );
}
