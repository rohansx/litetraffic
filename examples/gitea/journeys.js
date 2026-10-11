import http from "k6/http";
import * as lt from "./litetraffic/runtime.js";

export const options = lt.options();

// One journey files one issue and reads it back; the final observation checks the repo counted every one.
export default function fileIssue() {
  const key = lt.journeyKey();
  const fixture = JSON.parse(__ENV.LT_FIXTURE_JSON || '{"owner":"alice","repo":"lt-issues"}');
  const base = `${__ENV.LT_TARGET}/api/v1/repos/${fixture.owner}/${fixture.repo}/issues`;
  const headers = {
    "Content-Type": "application/json",
    Authorization: `token ${__ENV.GITEA_TOKEN_ALICE}`,
    "X-LiteTraffic-Run": __ENV.LT_RUN_ID,
  };
  const title = `lt ${key}`;
  const created = http.post(base, JSON.stringify({ title, body: `run ${__ENV.LT_RUN_ID}` }), {
    headers,
    tags: { operation: "create_issue" },
  });
  let number = null;
  try {
    number = created.json("number");
  } catch (_) {}
  if (created.status === 201 && number) lt.stage("created");
  const read = number
    ? http.get(`${base}/${number}`, { headers, tags: { operation: "get_issue" } })
    : { status: 0, json: () => null };
  let body = null;
  try {
    body = read.json();
  } catch (_) {}
  if (read.status === 200) lt.stage("read_back");
  lt.evidence("issue_persists", created.status === 201 && read.status === 200, {
    expected: { create: 201, read: 200 },
    actual: { create: created.status, read: read.status },
  });
  lt.evidence("title_round_trips", body !== null && body.title === title && body.state === "open", {
    expected: { title, state: "open" },
    actual: body ? { title: body.title, state: body.state } : null,
  });
}
