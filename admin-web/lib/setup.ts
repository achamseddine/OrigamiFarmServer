import { SetupStep } from "./types";

/** What each first-run step is called, in the operator's words rather than
 *  the API's keys, and the screen that completes it.
 *
 * Shared rather than repeated per screen: the Getting started guide and the
 * Overview banner name the same outstanding step, and two copies of this
 * would eventually name it differently.
 */
export const SETUP_STEPS: Record<SetupStep["key"], { title: string; action: string; href: string }> =
  {
    password: {
      title: "Set your own password",
      action: "Change your password",
      href: "/account",
    },
    staff: { title: "Add your team", action: "Add a colleague", href: "/staff" },
    pricing: {
      title: "Set what Origami costs",
      action: "Set the price",
      href: "/catalog",
    },
    tenant: {
      title: "Create your first customer",
      action: "Create your first customer",
      href: "/tenants/new",
    },
    subscription: {
      title: "Record what a customer pays",
      action: "Open Tenants",
      href: "/tenants",
    },
    // Done by a farmer rather than by an admin: a tablet appears the first
    // time somebody signs in on it. Tenants is still where you look.
    device: { title: "See a tablet sign in", action: "Open Tenants", href: "/tenants" },
  };
