const ALLOWED = new Set(["first_name", "name", "login", "email"]);
const TOKEN = /\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g;

export type MessageContact = {
  name: string | null;
  login: string;
  public_email: string | null;
};

export function unknownPlaceholders(...templates: string[]): string[] {
  const found: string[] = [];
  for (const template of templates) {
    for (const match of template.matchAll(TOKEN)) {
      const name = match[1];
      if (!ALLOWED.has(name) && !found.includes(name)) {
        found.push(name);
      }
    }
  }
  return found;
}

export function placeholderValues(contact: MessageContact): Record<string, string> {
  const name = (contact.name || "").trim().replace(/\s+/g, " ");
  return {
    first_name: name ? name.split(" ")[0] : contact.login,
    name: name || contact.login,
    login: contact.login,
    email: contact.public_email || "",
  };
}

export function renderMessage(template: string, contact: MessageContact): string {
  const values = placeholderValues(contact);
  return template.replace(TOKEN, (whole, name: string) => values[name] ?? whole);
}
