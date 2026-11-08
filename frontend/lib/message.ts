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
      if (name && !ALLOWED.has(name) && !found.includes(name)) {
        found.push(name);
      }
    }
  }
  return found;
}

export function placeholderValues(contact: MessageContact): Record<string, string> {
  const name = (contact.name || "").trim().replace(/\s+/g, " ");
  return {
    first_name: (name ? name.split(" ")[0] : undefined) ?? contact.login,
    name: name || contact.login,
    login: contact.login,
    email: contact.public_email || "",
  };
}

// Mirrors `_HTML_TAG` in backend/app/mail.py: a real tag, not `<a@example.com>`.
const HTML_TAG =
  /<(?:!doctype\s+html|\/?(?:html|head|body|p|div|span|br|hr|a|b|i|u|em|strong|ul|ol|li|table|tr|td|th|h[1-6]|img|font|center|blockquote|style))(?=[\s/>])[^<>]*>/i;

/** True when the body is written as HTML. The server then sends it as HTML. */
export function isHtml(body: string): boolean {
  return HTML_TAG.test(body);
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#x27;");
}

export function renderMessage(
  template: string,
  contact: MessageContact,
  options: { escapeHtml?: boolean } = {},
): string {
  const values = placeholderValues(contact);
  return template.replace(TOKEN, (whole, name: string) => {
    const value = values[name];
    if (value === undefined) {
      return whole;
    }
    return options.escapeHtml ? escapeHtml(value) : value;
  });
}
