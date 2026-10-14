type Recipient = { contact_id?: string | null; contact_name: string };
type Task = { id: string; recipients: Recipient[] };
type Item = Recipient & { status: string };
type Run = { task_id: string; items: Item[] };
type Contact = { id: string; display_name: string };

// Legacy records predate contact_id. Resolve names only when unambiguous
// within that task, including disabled recipients. Never override an explicit ID.
export function todaySentKeys(
  tasks: Task[],
  runs: Run[],
  contacts: Contact[],
): Set<string> {
  const sent = new Set<string>();
  const names = new Map<string, Map<string, Set<string>>>();
  const displayNames = new Map(
    contacts.map((contact) => [contact.id, contact.display_name]),
  );
  for (const task of tasks) {
    const aliases = new Map<string, Set<string>>();
    for (const recipient of task.recipients) {
      const key = recipient.contact_id || recipient.contact_name;
      for (const name of [
        recipient.contact_name,
        recipient.contact_id && displayNames.get(recipient.contact_id),
      ]) {
        if (!name) continue;
        const keys = aliases.get(name) || new Set<string>();
        keys.add(key);
        aliases.set(name, keys);
      }
    }
    names.set(task.id, aliases);
  }
  for (const run of runs) {
    for (const item of run.items) {
      if (item.status !== 'submitted') continue;
      if (item.contact_id) {
        sent.add(`${run.task_id}:${item.contact_id}`);
      } else {
        const candidates = names.get(run.task_id)?.get(item.contact_name);
        if (candidates?.size === 1)
          sent.add(`${run.task_id}:${[...candidates][0]}`);
      }
    }
  }
  return sent;
}
