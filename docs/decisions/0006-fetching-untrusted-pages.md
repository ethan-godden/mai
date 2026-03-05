# 0006: Rules for fetching untrusted pages

Date: 2026-11-23
Status: Accepted

## Context

Mai fetches URLs that come from search results and, later, from links inside
pages. Neither is trustworthy. Without limits, a crafted result or redirect
could make Mai request services on the user's own machine or network (the
model server on port 11434, the SearXNG instance, a router's admin page), and
a headless browser running a page's scripts widens that risk. Mai also sends
automated requests to other people's sites, which carries an obligation to
behave well.

## Decision

1. **Refuse private and local addresses** in the fetcher: loopback, private
   ranges, link-local, single-label hostnames, and `.local`-style names. The
   check runs on every redirect hop. It is on by default and can be turned
   off with `fetch.allow_private_addresses`.
2. **Apply the same check to every request made inside the browser**, by
   intercepting requests, so page scripts are covered too.
3. **Obey robots.txt**, pace requests per host, and cap concurrency.
4. **Identify honestly** with a `Mai/...` user agent rather than imitating a
   browser.
5. **Use the browser only for pages that were served but contain no text
   until scripts run.** It is never used to retry a page that was refused
   (robots.txt, HTTP 4xx, a blocked address).
6. **Bound every fetch**: timeout, size limit, redirect limit, and a short
   list of accepted content types.

## Alternatives considered

- **Imitate a browser's user agent**: more pages would load, since some sites
  refuse unknown clients. Rejected: it is deception aimed at the sites'
  access controls, and it invites an arms race.
- **Use the browser for everything**: simpler, and it handles every page, but
  it is several times slower and heavier, and runs untrusted scripts for
  every source instead of the few that need it.
- **Resolve DNS and check the resolved address**: closes the gap where a
  public name points at a private address, but needs a custom transport to
  pin the connection to the checked address. Deferred; noted as a known
  limit.
- **No address restrictions** (it is a local, single-user tool): rejected
  because the URLs are attacker-influenced even though the user is trusted.

## Consequences

- Some pages will not be readable: those that refuse unknown clients or
  disallow crawlers. Reports list sources that could not be read.
- Local documentation cannot be fetched unless the user opts in.
- A DNS-rebinding style attack is not prevented.
- Search providers are configured by the user and are exempt: the local
  SearXNG instance is reached on localhost by design.
