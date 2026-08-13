# SaaS Readiness — What's Missing

Build Mill today is a single-tenant-operated, multi-org app: RLS-scoped orgs, roles,
platform-provisioned agent pools, and *internal* LLM-usage metering (Phase 33/52/53 —
paying Anthropic for agent runs). None of that is the same as **selling Build Mill
itself as a subscription product** to outside customers. This is a gap analysis for that
jump, grouped by area, each item a 1–2 line brief. Not scoped into stories yet — pick
what to formalize first.

## 1. Billing & monetization (customer-facing, not LLM metering)

- [ ] **Stripe (or equivalent) integration** — no payment processor is wired in anywhere; orgs cannot be charged today.
- [ ] **Pricing plans / tiers** — no `pricing_plans` table or tier concept (Free/Pro/Enterprise); today every org gets the same fixed quota (org quota 3, Phase 57).
- [ ] **Subscription lifecycle** — trial start/end, upgrade/downgrade, cancellation, dunning on failed payment, none of this exists.
- [ ] **Usage-based add-on billing** — Phase 33 meters LLM spend per org but never turns it into an invoice line; there's no bridge from `llm_usage` to a billable charge.
- [ ] **Invoices & receipts** — no invoice history, no downloadable receipts, no tax handling (VAT/sales tax).
- [ ] **Seat-based billing** — team/member counts aren't tied to a per-seat price anywhere.
- [ ] **Self-serve plan management UI** — no "Billing" settings page for a customer to see/change their plan or payment method.

## 2. Tenant onboarding & self-service signup

- [ ] **Public signup flow** — org creation today is effectively operator-provisioned; there's no "sign up, create your org, verify email" funnel for strangers.
- [ ] **Email verification** — not confirmed to exist as a gate before granting access.
- [ ] **Guided onboarding** — no first-run checklist (connect GitHub, connect an LLM key, invite teammates) for a brand-new org.
- [ ] **Org deletion / offboarding self-service** — no customer-initiated "delete my org and all data" flow (only manual/DB-level today).
- [ ] **Domain-based org discovery / SSO-friendly signup** — no "join your company's existing org" prompt by email domain.

## 3. Identity, auth & access hardening

- [ ] **SSO / SAML / OIDC for enterprise customers** — Supabase Auth is used directly; no enterprise identity federation.
- [ ] **MFA enforcement policy** — no per-org "require 2FA" setting.
- [ ] **Session management UI** — no "view/revoke active sessions" surface for a user or admin.
- [ ] **API keys for customers** — the platform issues worker tokens internally, but there's no customer-facing API key management for programmatic access to their own org's data.
- [ ] **Fine-grained audit log export** — capability grants exist (Phase 9) but there's no exportable, filterable audit trail a customer's security team could pull.

## 4. Multi-tenancy hardening at scale

- [ ] **Per-tenant resource isolation limits** — Phase 57 isolates agent pools, but there's no hard cap preventing one noisy-neighbor org from starving shared Supabase connections/API compute.
- [ ] **Tenant-level rate limiting** — grep shows only scattered `rate_limit` mentions in a couple of route files, not a systematic per-org API rate limiter.
- [ ] **Soft-delete / data retention policy per plan tier** — no defined retention window differentiator (e.g., free tier 30-day log retention vs. paid unlimited).
- [ ] **Region/data-residency options** — single Supabase project pair (prod/dev); no story for EU-resident data if that's ever required by a customer.

## 5. Operational maturity (SRE / reliability)

- [ ] **Status page** — no public uptime/incident status page (statuspage.io, Better Stack, or self-hosted).
- [ ] **SLA definition & monitoring** — no committed uptime target or alerting tied to one.
- [ ] **Structured on-call / alerting** — deploy health-checks exist in the GH Action, but there's no PagerDuty/Opsgenie-style alert routing for prod incidents outside deploy time.
- [ ] **Automated backups & restore drills** — Supabase has built-in backups, but there's no documented/tested restore runbook or RPO/RTO target.
- [ ] **Horizontal scaling story** — `factory-web`/`factory-api` run as single systemd services on one GCP VM; no load-balanced multi-instance deployment.
- [ ] **CI test gate** — the CI test workflow was deliberately removed 2026-07-30 (testing is local-only); fine for one operator-team, risky once external customers depend on uptime.
- [ ] **Staging/UAT branch isolation from prod data** — `uat` branch exists and auto-deploys, but confirm it doesn't share the prod Supabase project for a paying customer's data.

## 6. Compliance & trust

- [ ] **Terms of Service & Privacy Policy** — no legal docs found in the repo or referenced in the app.
- [ ] **GDPR/CCPA data subject request tooling** — no "export my data" / "delete my data" self-service flow beyond DB-level manual deletion.
- [ ] **SOC 2 / security questionnaire readiness** — no documented security controls, vendor list, or pen-test history that an enterprise buyer would ask for.
- [ ] **Secrets & subprocessor disclosure** — no page listing what third parties (Anthropic, GitHub, Supabase, GCP) process customer data.
- [ ] **DPA (Data Processing Agreement) template** — needed before any enterprise contract.

## 7. Customer-facing product surfaces

- [ ] **Public marketing/pricing site** — `apps/public` exists as a static marketing shell but has no pricing page, no signup CTA wired to a real flow.
- [ ] **In-app plan/usage dashboard** — Phase 33/60 give the superadmin and org usage views for LLM spend, but nothing frames it against "your plan's included quota" for a self-serve customer.
- [ ] **Support channel** — no in-app support widget, helpdesk (Zendesk/Intercom), or documented support SLA.
- [ ] **Product changelog / release notes surface for customers** — release notes are generated per-release (Versioning & Release section) but not confirmed to be customer-visible anywhere.
- [ ] **Notification preferences at the org/billing level** — Phase notification settings exist for work items, but not for billing events (payment failed, plan renewed, usage near limit).

## 8. Legal/business entity plumbing (non-code, flag only)

- [ ] **Business entity, payment processor merchant account, and tax registration** — outside the codebase, but blocks turning on billing at all.
- [ ] **Vendor agreements with Anthropic/GitHub/Supabase/GCP at resale scale** — confirm current API terms permit reselling usage to third-party customers (some LLM/API ToS restrict resale).

---

**Suggested first slice**: Stripe + pricing tiers + self-serve signup (items 1 & 2) are
the minimum to take a single paying customer; SSO, compliance docs (3 & 6) can follow
once there's a first design partner asking for them. Operational maturity (5) matters
in proportion to how much external trust is being asked for at each stage.
