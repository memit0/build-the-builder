// Mock of Freya Bank's statement services (service contract 2026-10-01, section 6).
// Serves test-customers.json verbatim: same status codes and bodies.
import DB from "./test-customers.json";

const BY_CUSTOMER = ["cards.list", "contact.email.get", "statement.pdf.send"];
const BY_WEEK = ["statement.week.decide", "statement.week.change"];
const NOT_FOUND = {
  "cards.list": "FB_IVR_CHANNEL_102",
  "cards.balance": "FB_IVR_CHANNEL_120",
  "statement.week.get": "FB_IVR_CHANNEL_121",
  "statement.pdf.send": "FB_IVR_CHANNEL_107",
};
const NOT_FOUND_TEXT = {
  "FB_IVR_CHANNEL_102": "Müşteriye ait geçerli kart bilgisi bulunamadı.",
  "FB_IVR_CHANNEL_120": "Kart bakiye servisi hatası: kart bulunamadı",
  "FB_IVR_CHANNEL_121": "Ekstre hafta servisi hatası: kart bulunamadı",
  "FB_IVR_CHANNEL_107": "Pdf dokümanı bulunamadı",
};
const REQUIRED = {
  "cards.list": ["customerNo"],
  "cards.balance": ["cardNumber"],
  "statement.week.get": ["cardNumber"],
  "statement.week.decide": ["cardNumber", "newStatementWeek"],
  "statement.week.change": ["cardNumber", "newStatementWeek"],
  "contact.email.get": ["customerNo", "intAddressInfoType"],
  "statement.pdf.send": ["customerNo", "cardNumber", "currencyType", "statementDate", "emailAddress"],
};

// Flatten: service -> key -> entry (card-keyed services are nested under each customer).
const INDEX = {};
for (const c of DB.customers) {
  for (const [svc, v] of Object.entries(c.services)) {
    INDEX[svc] ??= {};
    if (BY_CUSTOMER.includes(svc)) INDEX[svc][c.customerNo] = v;
    else Object.assign(INDEX[svc], v);
  }
}
const SAMPLE = { customerNo: "90000001", cardNumber: "540000******1234" };

const guid = () => crypto.randomUUID().replace(/-/g, "");
function error(http, code, text, type, rootCause) {
  const g = guid();
  return Response.json({ result: {
    guid: g, returnCode: code, messageText: text, code, message: text, type,
    details: { guid: g, rootCause, errorOrigin: "freya-bank-mock" },
  } }, { status: http });
}
const invalid = (field) => error(400, "INVALID_ARGUMENTS_ERROR", `${field} cannot be empty`, "ERROR", "InvalidArgumentsException");
const isTemplate = (v) => typeof v === "string" && (v.startsWith("{{") || v.startsWith("%7B%7B"));
const str = (v) => (v === undefined || v === null ? "" : String(v).trim());

async function serve(svc, request) {
  let body = {};
  try { body = await request.json(); } catch {}
  const data = (body && body.data) || {};
  for (const f of ["customerNo", "cardNumber"]) if (isTemplate(data[f])) data[f] = SAMPLE[f];
  for (const f of REQUIRED[svc]) if (str(data[f]) === "") return invalid(f);
  let entry = INDEX[svc][str(BY_CUSTOMER.includes(svc) ? data.customerNo : data.cardNumber)];
  if (entry && BY_WEEK.includes(svc)) entry = entry[str(data.newStatementWeek)];
  if (!entry) {
    const code = NOT_FOUND[svc];
    return code ? error(404, code, NOT_FOUND_TEXT[code], "WARNING", "NotFoundException") : invalid(BY_WEEK.includes(svc) ? "newStatementWeek" : "cardNumber");
  }
  if (entry.delaySeconds) await new Promise((r) => setTimeout(r, entry.delaySeconds * 1000));
  return Response.json(entry.response, { status: entry.http });
}

export default {
  async fetch(request) {
    const path = new URL(request.url).pathname.replace(/\/+$/, "");
    if (request.method === "GET" && path === "/health") return Response.json({ durum: "ok" });
    if (request.method === "POST" && path === "/oauth2/token")
      return Response.json({ access_token: "test-token", token_type: "Bearer", expires_in: 1800 });
    const svc = path.replace(/^\/voice\/v1\//, "");
    if (request.method === "POST" && path.startsWith("/voice/v1/") && REQUIRED[svc]) return serve(svc, request);
    return Response.json({ hata: "bulunamadı", yollar: Object.keys(REQUIRED).map((s) => `POST /voice/v1/${s}`) }, { status: 404 });
  },
};
