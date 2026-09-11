"use client";

import { Fragment } from "react";
import Link from "next/link";
import { ErrorBanner, Loading, PageHeader, useResource } from "@/lib/ui";
import { SetupState, SetupStep } from "@/lib/types";
import { SETUP_STEPS } from "@/lib/setup";

/** One box in the flow.
 *
 * `key` ties a box to a step the platform can actually check
 * (GET /platform/v1/setup); a box without one is either a fact that is
 * true by the time anyone reads this screen, or a habit with no finish
 * line. Keeping the two kinds visually distinct is the point: a tick that
 * was never verified is worse than no tick at all.
 */
interface Node {
  /** The setup steps this box stands for — more than one where a single
   *  job covers several (building a price list is creating the plans and
   *  pricing them, on one screen). The box is done only when all of them
   *  are. */
  keys?: SetupStep["key"][];
  title: string;
  detail: string;
  href?: string;
  /** Shown in place of a live state for boxes nothing can check. */
  note?: string;
}

const PHASE_ONE: Node[] = [
  {
    title: "Sign in",
    detail:
      "The very first account is created by whoever installed Origami — there is no sign-up page, on purpose.",
    note: "Done — this is you",
  },
  {
    keys: ["password"],
    title: "Set your own password",
    detail: "Replace the one you were handed with something only you know.",
    href: "/account",
  },
  {
    keys: ["staff"],
    title: "Add your team",
    detail:
      "Give each colleague the smallest role that lets them work. Keep super admin to as few people as possible.",
    href: "/staff",
  },
  {
    keys: ["plans", "pricing"],
    title: "Build your price list",
    detail:
      "The packages you sell, each with a price. Type normal money: 249 means 249, not cents. Nothing can report revenue until you do.",
    href: "/catalog",
  },
];

const PHASE_TWO: Node[] = [
  {
    keys: ["tenant"],
    title: "Create the customer",
    detail:
      "Five steps: their business, their first site, the features they bought, who their boss is, confirm.",
    href: "/tenants/new",
  },
  {
    keys: ["subscription"],
    title: "Record what they pay",
    detail:
      "Their plan, monthly or yearly, and the status — trial while they evaluate, active once they pay. The step people forget.",
    href: "/tenants",
  },
  {
    keys: ["device"],
    title: "Send the tablet code",
    detail:
      "A one-time code on the customer's Devices tab. Their worker types it into the app to pair it.",
    href: "/tenants",
  },
  {
    title: "Check it landed",
    detail:
      "A day or two later, look for their name. Zero records a fortnight in is a phone call, not something to wait out.",
    href: "/usage",
    note: "Ongoing",
  },
];

const PHASE_THREE: Node[] = [
  {
    title: "Each morning",
    detail: "Overview. Thirty seconds: is everything running, did anything change overnight?",
    href: "/dashboard",
    note: "Daily",
  },
  {
    title: "Each week",
    detail: "Business, then Usage. What are we earning, who renews soon, is anyone past due?",
    href: "/business",
    note: "Weekly",
  },
  {
    title: "Before a renewal call",
    detail:
      "The customer's Usage tab. Walk in knowing what they record and what they never touch.",
    href: "/usage",
    note: "Per customer",
  },
  {
    title: "When something looks wrong",
    detail: "Audit log. Every consequential action, with who did it and why. Nothing is ever edited out.",
    href: "/audit",
    note: "As needed",
  },
];

function NodeBox({ node, states }: { node: Node; states: SetupStep[] }) {
  const checked = states.length > 0;
  const done = checked && states.every((state) => state.done);
  const kind = checked ? (done ? "done" : "todo") : "habit";

  const body = (
    <>
      <span className="marker">{checked ? (done ? "✓ Done" : "To do") : node.note ?? ""}</span>
      <span className="t">{node.title}</span>
      <span className="d">{node.detail}</span>
      {checked && <span className="s">{states.map((state) => state.detail).join(" · ")}</span>}
    </>
  );

  const className = `flow-node ${kind}`;
  return node.href ? (
    <Link href={node.href} className={className}>
      {body}
    </Link>
  ) : (
    <div className={className}>{body}</div>
  );
}

function Lane({ nodes, steps }: { nodes: Node[]; steps: Map<string, SetupStep> }) {
  return (
    <div className="flow-lane">
      {nodes.map((node, index) => (
        <Fragment key={node.title}>
          {index > 0 && (
            <span className="flow-arrow" aria-hidden="true">
              →
            </span>
          )}
          <NodeBox
            node={node}
            states={(node.keys ?? []).flatMap((key) => {
              const state = steps.get(key);
              return state ? [state] : [];
            })}
          />
        </Fragment>
      ))}
    </div>
  );
}

export default function GuidePage() {
  const { data, error, loading } = useResource<SetupState>("/platform/v1/setup");

  const steps = new Map((data?.steps ?? []).map((step) => [step.key, step]));
  const outstanding = (data?.steps ?? []).filter((step) => !step.done);
  const next = outstanding[0];

  return (
    <div>
      <PageHeader
        title="Getting started"
        subtitle="How Origami is run, in the order you do it. Every box below is checked against this platform's own data, so it says where you have actually got to — not where a document assumed you would be."
      />
      <ErrorBanner message={error} />
      {loading && <Loading what="your setup" />}

      {data && (
        <>
          {next ? (
            <div className="next-step">
              <div>
                <div className="k">Do this next</div>
                <h2>{SETUP_STEPS[next.key].title}</h2>
                <p>
                  {next.detail}. {data.steps_done} of {data.steps_total} first-time steps done
                  {outstanding.length > 1 && `, ${outstanding.length - 1} after this one`}.
                </p>
              </div>
              <Link className="btn" href={SETUP_STEPS[next.key].href}>
                {SETUP_STEPS[next.key].action}
              </Link>
            </div>
          ) : (
            <div className="next-step">
              <div>
                <div className="k">Setup</div>
                <h2>You are set up</h2>
                <p>
                  All {data.steps_total} first-time steps are done. From here the job is the
                  rhythm in phase three — and repeating phase two for each new customer.
                </p>
              </div>
              <Link className="btn" href="/dashboard">
                Open Overview
              </Link>
            </div>
          )}

          <div className="panel">
            <div className="chart-title">First-time setup</div>
            <div className="chart-note">
              {data.steps_done} of {data.steps_total} done
            </div>
            <div className="setup-bar">
              <div
                className="fill"
                style={{ width: `${(data.steps_done / data.steps_total) * 100}%` }}
              />
            </div>
          </div>

          <div className="flow-phase">
            <div className="flow-phase-head">
              <span className="tag">Phase one</span>
              <h3>Set yourself up</h3>
            </div>
            <p className="cadence">Done once, before you have any customers. About half an hour.</p>
            <Lane nodes={PHASE_ONE} steps={steps} />
          </div>

          <div className="flow-phase">
            <div className="flow-phase-head">
              <span className="tag">Phase two</span>
              <h3>Sign a customer</h3>
            </div>
            <p className="cadence">
              Repeated for every farm you take on — ten minutes each. These boxes go back to
              &ldquo;to do&rdquo; when a new customer is missing one of them.
            </p>
            <Lane nodes={PHASE_TWO} steps={steps} />
          </div>

          <div className="flow-phase">
            <div className="flow-phase-head">
              <span className="tag">Phase three</span>
              <h3>Settle into a rhythm</h3>
            </div>
            <p className="cadence">
              Once customers are live this is the whole job. Nothing here is ever &ldquo;done&rdquo;.
            </p>
            <Lane nodes={PHASE_THREE} steps={steps} />
          </div>
        </>
      )}

      <div className="panel">
        <div className="chart-title" style={{ marginBottom: 4 }}>
          How the pieces fit
        </div>
        <div className="chart-note">
          Who holds what. You never work inside a customer&rsquo;s farm data from here — you decide
          what they may use, and their own people record the work.
        </div>
        <div className="pieces">
          <div className="piece you">
            <div className="w">You — Origami staff</div>
            <p>This console. Customers, plans, prices, access, licences, dashboards.</p>
          </div>
          <span className="flow-arrow" aria-hidden="true">
            →
          </span>
          <div className="piece">
            <div className="w">A customer (tenant)</div>
            <p>
              One farming business. Their data is fenced off from every other customer&rsquo;s by the
              database itself, not by a filter someone could forget.
            </p>
          </div>
          <span className="flow-arrow" aria-hidden="true">
            →
          </span>
          <div className="piece">
            <div className="w">Their farms and people</div>
            <p>
              The customer&rsquo;s own boss adds their staff and decides who can do what on which
              site.
            </p>
          </div>
          <span className="flow-arrow" aria-hidden="true">
            →
          </span>
          <div className="piece">
            <div className="w">Tablets in the field</div>
            <p>
              Paired with a one-time code, then they work offline and sync. What they may open is
              the licence you granted.
            </p>
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="chart-title" style={{ marginBottom: 12 }}>
          Words you will see
        </div>
        <div className="glossary">
          <div>
            <div className="w">Tenant</div>
            <p>One customer — a whole farming business, which may have several farm sites.</p>
          </div>
          <div>
            <div className="w">Module</div>
            <p>A feature area: animals, milk, crops, sales. What a customer buys.</p>
          </div>
          <div>
            <div className="w">Entitlement</div>
            <p>The record that a customer is allowed to use a module. Granted and withdrawn here.</p>
          </div>
          <div>
            <div className="w">Plan &amp; subscription</div>
            <p>
              The plan is what you sell; the subscription is one customer on it, with a price and a
              renewal date.
            </p>
          </div>
          <div>
            <div className="w">Licence lease</div>
            <p>
              A signed permission slip a tablet carries so it keeps working with no signal. It
              expires, so a withdrawn module cannot be used indefinitely.
            </p>
          </div>
          <div>
            <div className="w">Suspend vs terminate</div>
            <p>
              Suspend is reversible and still lets them read and export. Terminate closes the
              account for good.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
