/* Family portal: picks the page from <body data-portal="…">. */
import { fail } from "./core.js";
import * as account from "./account.js";

const PAGES = {
  "register": account.registerChooser,
  "register-details": account.registerDetails,
  "login": account.login,
  "forgot": account.forgot,
  "reset": account.reset,
  "activate": account.activate,
  "account": account.accountHome,
  "child-new": account.childNew,
  "child": account.childPage,
  "details": account.details,
};

const page = document.body.dataset.portal;
Promise.resolve()
  .then(() => (PAGES[page] ? PAGES[page]() : fail(new Error("Page not found"))))
  .catch(fail);
