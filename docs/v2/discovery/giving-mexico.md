# Mexico-first giving and reconciliation — discovery draft (issue #86)

**Status:** draft for a meeting with church administration and accounting. Nothing here is decided, and
nothing here is legal or tax advice. The go/no-go belongs to church administration, with legal and
accounting review (decision 0014); decision 0011 already says no financial processing is built before that.
**Date:** 2026-11-17.

**Evidence labels.** **V** = read on the official page on 2026-11-17. **S** = search-result summary or a
third-party page; the official page was not readable. **I** = my inference or proposal. Prices are list
prices on that date and change; confirm them on the day of the decision.

## 1. What "giving" could mean

| Scope | What Emmaus does | Data in Emmaus | Verdict (I) |
|---|---|---|---|
| 0. Text block | The public page shows the church's bank details as text the administration typed | None | "No-go for now" still delivers this |
| 1. Link out | A button «Dar» opens a payment page hosted by the provider, in the church's own provider account | Only the URL | Smallest "go" |
| 2. Recorded gifts | Emmaus creates the hosted session, receives signed callbacks and keeps a ledger by fund | Section 5 | Only after step 1 has been reconciled for a few months |
| 3. Recurring gifts, giver history, receipts by e-mail | Subscriptions, a "my giving" page, statements | Giver identity over time | Later, each with its own approval |
| Out | Card forms inside Emmaus, stored cards, Emmaus or the platform owner holding or forwarding money, issuing CFDI, pledge campaigns, giving rankings, reminders to people who "stopped giving" | — | Never, or a separate decision |

Two fixed points (I): the merchant account, the bank account and the tax identity are **the church's**;
and card data never touches Emmaus. The site's content policy already forbids embeds, so the page is
opened on the provider's site, not framed.

## 2. Payment methods

| Method | How the giver pays | Confirmation | Refund / dispute | Notes |
|---|---|---|---|---|
| Card | Provider's hosted page | Seconds | Refundable; chargebacks possible for about 120 days [5] | Highest fee |
| SPEI transfer | From the bank app to a CLABE | Seconds to minutes | Refund possible; no chargeback for MXN on Stripe [4] | Direct to the church's own CLABE costs the church nothing in provider fees (scope 0) |
| Cash by reference (OXXO and others) | Voucher, paid at the counter | By the next business day on Stripe [3] | No refunds, no disputes on Stripe [3] | Reaches people without a card; 10 to 10,000 MXN per voucher on Stripe [3] |
| CoDi / DiMo | QR or phone number, over SPEI | Seconds | — | Banxico: no additional commissions, 24x7 [11]. An 8,000 MXN cap and DiMo's terms are S only. No hosted provider flow was found |
| Wallets | Mercado Pago balance, PayPal | Seconds | Per provider | Comes with those providers |

## 3. Providers with a hosted flow

Fees exclude IVA unless noted. "Not stated" means the page read did not say it.

| | Cards | Cash | SPEI | Dispute fee | Settlement | Evidence |
|---|---|---|---|---|---|---|
| Stripe (Payment Links, Checkout) | 3.6% + MXN 3; +0.5% international | 4% + MXN 3 | MXN 7 per transfer; refund MXN 7 | MXN 150, may be returned to Mexican businesses if won | First payout 7 calendar days, then 3 business days; OXXO up to T+4 | V [1][2][3][5][6] |
| Mercado Pago (Link de pago) | 3.49% + MXN 4 at once; 3.19% at 7 days; 2.95% at 30 days | 3.79% + MXN 4 in the FAQ, 3.49% in the price block of the same page | Same as cards | Not stated | Chosen by the seller, see fee | V [8]; help and developer pages 403/404 |
| Conekta (link, checkout) | 3.4% + MXN 3 | 2.6% + MXN 3 (minimum MXN 5.40), plus a counter fee paid by the giver (page inconsistent: 7–13 MXN) | MXN 12.50 | Not stated | First deposit within 10 business days; later schedule not stated | V [9] |
| Openpay (BBVA) | 2.9% + MXN 2.50 | 2.9% + MXN 2.50 | MXN 8 | Not found | Not found | S only; official price page returned no content |
| Clip (pagos a distancia) | 3.6% | — | — | Not found | 48 hours | S only; official URLs 404 |
| PayPal | 3.95% + MXN 4 below MXN 49,999 a month; a charity rate of 2.95% + fixed fee is listed | — | — | Not found | Not found | S only; official pages 403 |

Further verified points. Stripe Payment Links are a hosted page where the payer can choose the amount, and
Stripe names donations as a use; but bank transfers are **not** available on Payment Links, only on
Checkout with a customer record [4][7]. Stripe refunds do not return the original fee [1]. Callbacks on
Stripe are signed, retried for up to three days, may arrive twice and in any order [10]. A dispute debits
the amount plus the fee at once; the answer is due in 7 to 21 days and the whole process takes 2 to 3
months [5].

**Not established for any provider:** onboarding requirements for a religious association (which documents,
whether the category is accepted, whether a charity rate applies). Ask each shortlisted provider in writing.

Worked example (my arithmetic, assuming IVA 16% on the fee): a MXN 500 card gift on Stripe list price costs
18.00 + 3.00 = 21.00, plus IVA 3.36: the church receives MXN 475.64. The same gift by direct SPEI to the
church's CLABE arrives whole.

## 4. Receipts, tax and anti-money-laundering — what to confirm, not what to assume

- **Acknowledgement versus deductible receipt.** A provider's payment receipt or a «Gracias, recibimos tu
  aportación» from the church is an acknowledgement. A deductible receipt is a CFDI de donativo. SAT states
  that donatarias autorizadas must issue CFDI for the donations they receive and refers throughout to the
  "autorización para recibir donativos deducibles" (V [12]); the CFDI carries a donatarias complement with
  the authorization number (S [13]). **I:** unless the church holds that authorization, it must not offer
  or imply deductibility. Proposed wording until the accountant says otherwise: «Tu aportación no es
  deducible de impuestos.»
- **Religious associations.** Secondary sources only (S [14]): asociaciones religiosas fall under Title III
  of the income tax law, with an annual SAT letter of administrative facilities, simplified receipts and a
  periodic global CFDI for offerings. I could not open the current official letter. **The accountant must
  confirm:** the church's legal form and registration; whether it is a donataria autorizada; the letter in
  force this year; how an online gift from an identified person is documented (global or individual CFDI);
  how provider fees and their IVA are recorded; whether the provider invoices the church; and which word
  the church may use («ofrenda», «diezmo», «aportación», «donativo»).
- **Anti-money-laundering.** SAT's vulnerable-activities page lists the receipt of donations by non-profit
  associations and societies with an identification threshold of 1,605 UMA and a notice threshold of
  3,210 UMA (V [15]). The article was reformed on 2025-07-16 and a search summary of a sector communiqué
  gave a different notice figure (S [16]). **The accountant or a lawyer must confirm** whether this applies
  to the church, the thresholds in force, the peso value, whether gifts by one person are added up over a
  period, and what identification is then required. **I:** if it applies, a hosted page that accepts
  anonymous gifts needs a per-gift cap below the identification threshold.
- **Privacy.** A gift tied to a name reveals religious belief; religious beliefs are among sensitive personal
  data in the federal law of 2025 (S [17]). The privacy adviser confirms the notice, the consent and the
  retention period.

## 5. What Emmaus would store at scope 2 (proposal, I)

Per gift: church, fund, amount in centavos, currency `MXN`, status, provider, the provider's payment
reference, provider fee and net when reported, payout reference, and the times it was created, paid,
refunded or disputed. Per callback: the provider's event id (unique), type, time received and processed.
**Never:** card number, expiry, CVV, bank account of the giver, the provider's raw payload with personal
data, a free-text note. The giver's identity stays at the provider by default; linking a gift to a person
is scope 3. **Who sees it:** the church administration and the person it names as treasurer; an audit row
for every view of a list and every export. Pastors, group leaders and the platform operator see nothing;
dashboards show totals by fund and never a breakdown below the reporting minimum of 5 (decision 0015).

## 6. Reconciliation walk-through (synthetic; run it in the provider's test mode before any go)

Fund «Ofrenda general». Fees are the Stripe list prices above with IVA 16% assumed.

| # | Case | What happens | Emmaus must show | Passes when |
|---|---|---|---|---|
| 1 | One gift | D-001, MXN 500 by card. Callback "paid", event `evt_A` | D-001 `pagado`, 500.00, reference stored | The reference matches the provider's report line |
| 2 | Duplicate callback | `evt_A` arrives again; then a second event object for the same payment | Still one gift, one total | Event id is unique; the transition is idempotent; a late "created" after "paid" does not move it back |
| 3 | Forged callback | A request without a valid signature | Nothing changes; rejected and logged | Signature checked on the raw body |
| 4 | Refund | The treasurer refunds D-002 (MXN 500) in the provider's dashboard | D-002 `reembolsado`; the fee of 24.36 stays a cost | Totals drop by 500, not by 475.64 |
| 5 | Chargeback | The bank disputes D-003 (MXN 500): 650 is debited (500 + 150 fee) | D-003 `en disputa`, then `contracargo` or `pagado` again; the treasurer is told; the giver is never contacted automatically | Both outcomes handled; a dispute on a refunded gift is flagged |
| 6 | Cash two days later | D-004, MXN 200 voucher issued Friday, paid Sunday, confirmed Monday | `pendiente` until Monday, then `pagado` dated by the provider's payment time; an unpaid voucher becomes `vencido` after its expiry (5 days by default on Stripe [3]) and counts nowhere | No total includes a pending or expired voucher |
| 7 | Month end | Provider report against Emmaus against the bank statement | See below | The three agree to the centavo, by payout |

Month of the example: ten card gifts of 500 and one cash gift of 200 were paid: gross **5,200.00**. Fees:
10 × 24.36 + 12.76 = **256.36**. Refund **500.00**. Dispute **650.00**. Expected in the bank:
5,200.00 − 256.36 − 500.00 − 650.00 = **3,793.64**. Emmaus must show 9 gifts `pagado` (4,200.00), 1
`reembolsado`, 1 `en disputa`. Reconcile payout by payout, not by calendar month: gifts paid on the last
days settle in the next month, and cash settles later than cards. Any difference is listed as a line
("in the provider, not in Emmaus", "in Emmaus, not in the provider"), never corrected silently; a daily
comparison against the provider's API catches a callback that never arrived.

## 7. Go/no-go checklist for administration and accounting

1. The accountant has confirmed the tax treatment of online gifts and the wording about deductibility.
2. The anti-money-laundering question has a written answer, and a per-gift cap if needed.
3. A provider accepted the church under its own legal name and bank account, in writing, with its fees.
4. Someone is named treasurer, with a second person for refunds, and owns the monthly reconciliation.
5. A refund policy exists, including cash gifts, which the provider cannot refund.
6. The privacy notice covers giving and the privacy adviser has approved it.
7. The seven cases above passed in test mode and the result is attached to the issue.
8. The church knows what it costs: fees, IVA, the time of the monthly close, and what a dispute costs.

**If "go":** scope 1 only. The administration pastes the provider's hosted link into "Configuración de la
iglesia"; Emmaus shows a button and the non-deductibility line; reconciliation is the provider's report
against the bank; Emmaus stores no gift. **If "no-go for now":** scope 0, a text block with the bank,
account holder and CLABE as the administration typed them, with an audit row on every change and a second
person confirming it, since a changed CLABE is how such pages are abused. Record either answer in a
decision record.

## 8. Open decisions

| Decision | Options | Who decides | Recommended default and why |
|---|---|---|---|
| Go, or no-go for now | Scope 0 / 1 / 2 | Church administration, after legal and accounting review | Scope 0 now, scope 1 after the checklist: no money moves through software before the questions are answered |
| Provider | Section 3 | Church administration and accountant | Shortlist two and ask for onboarding terms in writing; fees alone do not decide |
| Methods | Cards / SPEI / cash / all | Church administration | Direct SPEI (scope 0) plus cards; add cash only with a refund policy |
| Who absorbs the fee | Church / giver may add it | Church administration, accountant | Church: simpler accounting, no pressure on the giver |
| Funds | One / several | Church administration | One at first; each fund is a reconciliation line |
| Deductible receipts | None / CFDI issued by the church's own invoicing | Accountant | None from Emmaus, ever; acknowledgement only |
| Anonymous or identified gifts | Provider keeps identity / Emmaus links to a person | Privacy adviser, church leadership | Identity stays at the provider: it reveals belief |
| Per-gift maximum | None / below the identification threshold | Accountant or lawyer | Set one until the threshold question is answered |
| Recurring gifts | No / provider-managed | Church administration | No in the first version: cancellations and failed charges need support |
| Retention of gift records | Per tax rules | Accountant, privacy adviser | To be stated by the accountant; not researched here |
| Who sees gifts | Section 5 | Church leadership, product owner | Administration and treasurer only, audited; a platform floor |

## Sources (all read 2026-11-17)

1. Stripe pricing, Mexico: https://stripe.com/mx/pricing
2. Stripe local payment methods pricing: https://stripe.com/es-mx/pricing/local-payment-methods
3. Stripe, OXXO: https://docs.stripe.com/payments/oxxo
4. Stripe, bank transfers: https://docs.stripe.com/payments/bank-transfers
5. Stripe, how disputes work: https://docs.stripe.com/disputes/how-disputes-work
6. Stripe, payouts: https://docs.stripe.com/payouts (first 100,000 characters read)
7. Stripe, Payment Links: https://docs.stripe.com/payment-links
8. Mercado Pago, Link de pago: https://www.mercadopago.com.mx/herramientas-para-vender/link-de-pago
9. Conekta pricing: https://www.conekta.com/pricing
10. Stripe, webhooks: https://docs.stripe.com/webhooks
11. Banco de México, CoDi: https://www.banxico.org.mx/sistemas-de-pago/codi-cobro-digital-banco-me.html
12. SAT, obligations of donatarias autorizadas: https://wwwmat.sat.gob.mx/consulta/70078/conoce-las-principales-obligaciones-fiscales-de-las-donatarias-autorizadas
13. SAT, CFDI de donatarias (PDF, not machine-readable here; search summary): https://www.sat.gob.mx/minisitio/DonatariasAutorizadas/documentos/CFDI_Donatarias2026.pdf
14. Secondary, religious associations (search summaries): https://idconline.mx/fiscal-contable/2022/06/08/asociaciones-religiosas-y-sus-obligaciones-fiscales and https://www.soyconta.com/obligaciones-de-las-asociaciones-religiosas/
15. SAT, Actividades Vulnerables: https://www.sat.gob.mx/minisitio/ActividadesVulnerables/index.html
16. Law text on a third-party site, marked there as unverified (same thresholds as [15], reform DOF 16-07-2025): https://mley.mx/LFPIORPI/articulo/17/ ; sector communiqué (search summary): https://www.cemefi.org/wp-content/uploads/2025/07/Ley-Antilavado_COMUNICADO_LFPIORPI_2025_v33.pdf
17. Federal data protection law of 2025 (search summary; official text not opened): https://www.diputados.gob.mx/LeyesBiblio/pdf/LFPDPPP.pdf

**Could not be read:** Mercado Pago help and developer pages (403, 404); PayPal Mexico fee pages (403);
Openpay price page (no content) ; Clip price pages (404); SAT PDFs [13] and the 2026 FAQ on vulnerable
activities (binary). Third-party figures for Openpay, Clip and PayPal: https://support.wix.com/en/article/connecting-openpay-as-a-payment-provider ,
https://www.latamfintech.co/articles/clip-lanza-pagos-a-distancia-y-presenta-su-plan-para-apoyar-a-las-mipymes-en-mexico ,
https://www.paypal.com/mx/webapps/mpp/merchant-fees (via search summary).
