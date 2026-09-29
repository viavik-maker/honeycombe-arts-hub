/* Family portal: picks the page from <body data-portal="…">. */
import { fail } from "./core.js";
import * as account from "./account.js";
import * as book from "./book.js";
import * as guest from "./guest.js";
import * as privacy from "./privacy.js";
import * as send from "./send.js";
import * as shop from "./shop.js";

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
  "book": book.book,
  "book-review": book.review,
  "book-done": book.done,
  "bookings": book.myBookings,
  "guest": guest.guest,
  "guest-confirm": guest.guestConfirm,
  "privacy": privacy.privacy,
  "unsubscribe": privacy.unsubscribe,
  "send": send.sendSupport,
  "carer-invite": account.carerInvite,
  "handover": account.handover,
  "shop": shop.shop,
};

const page = document.body.dataset.portal;
Promise.resolve()
  .then(() => (PAGES[page] ? PAGES[page]() : fail(new Error("Page not found"))))
  .catch(fail);
