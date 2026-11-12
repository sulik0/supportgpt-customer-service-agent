export function parseDemoAccounts(value) {
  if (!value?.trim()) return [];
  try {
    const accounts = JSON.parse(value);
    if (!Array.isArray(accounts)) return [];
    return accounts.filter(account => account && ['label', 'username', 'password'].every(key => typeof account[key] === 'string' && account[key].trim()));
  } catch {
    return [];
  }
}
