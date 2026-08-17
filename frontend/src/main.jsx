import React, { createContext, useContext, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { Navigate, NavLink, Route, BrowserRouter as Router, Routes, useLocation } from "react-router-dom";
import {
  Bell,
  BriefcaseBusiness,
  CalendarDays,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Edit3,
  Info,
  LogOut,
  Mail,
  Settings,
  Trash2,
  Users,
  X,
} from "lucide-react";
import {
  createAgent,
  createConfigRecord,
  deleteConfigRecord,
  deleteAgentRecord,
  deletePrompt,
  savePrompt,
  updateAgentRecord,
  updateConfigRecord,
  updatePrompt as updatePromptRecord,
} from "./services/dashboardApi.js";
import "./styles.css";

const navItems = [
  { label: "Dashboard", path: "/", icon: BriefcaseBusiness },
  { label: "Agents", path: "/agents", icon: Users },
  { label: "Business Actions", path: "/business-actions", icon: BriefcaseBusiness },
  { label: "Employee Engagements", path: "/employee-engagements", icon: Users },
];

const businessRows = [
  ["Email Intake", "Email Agent", "Active", "-", "-"],
  ["Customer Classification", "Email Agent", "Active", "-", "-"],
  ["Priority Assignment", "Email Agent", "In Review", "-", "-"],
  ["Task Routing", "Email Agent", "Active", "-", "-"],
];

const engagementRows = [
  ["Anika Rao", "Operations", "12", "9", "In Progress"],
  ["Ravi Menon", "Customer Support", "8", "8", "Complete"],
  ["Meera Shah", "Product", "7", "5", "In Progress"],
  ["Vikram Jain", "Engineering", "10", "6", "Active"],
];

const DEFAULT_AGENT_DRAFT = { title: "", description: "", status: "active" };
const EMPTY_CONFIGURATION = {
  sourceEmails: [],
  destinationEmails: [],
  watchEmails: [],
  keyCustomers: [],
  routes: [],
  classifications: [],
  teamsRouteConfigs: [],
};
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api").replace(/\/$/, "");
const FILTERED_EMPTY_MESSAGE = "No records found for selected date range. Clear filter to view all records.";
const CalendarFilterContext = createContext({
  activeDateFilter: null,
  applyDateFilter: () => {},
  clearDateFilter: () => {},
});

function useCalendarFilter() {
  return useContext(CalendarFilterContext);
}

function hasActiveDateFilter(dateFilter) {
  return Boolean(dateFilter?.startDate && dateFilter?.endDate);
}

function buildDateAwarePath(path, dateFilter, params = {}) {
  const queryParams = new URLSearchParams();

  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      queryParams.set(key, String(value));
    }
  });

  if (hasActiveDateFilter(dateFilter)) {
    queryParams.set("startDate", dateFilter.startDate);
    queryParams.set("endDate", dateFilter.endDate);
  }

  const queryString = queryParams.toString();
  return queryString ? `${path}?${queryString}` : path;
}

async function requestDateAware(path, dateFilter, params = {}) {
  const url = `${API_BASE_URL}${buildDateAwarePath(path, dateFilter, params)}`;
  console.log("API CALL", url);
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
    },
  });

  if (!response.ok) {
    const errorBody = await response.json().catch(() => null);
    throw new Error(errorBody?.error ?? `API request failed with status ${response.status}`);
  }

  return response.json();
}

function listAgents(dateFilter) {
  return requestDateAware("/agents", dateFilter);
}

function getReceivedEmails(dateFilter) {
  return requestDateAware("/emails/received", dateFilter);
}

function getProcessedEmails(dateFilter) {
  return requestDateAware("/emails/processed", dateFilter);
}

function getClientEmailSummary(dateFilter) {
  return requestDateAware("/emails/client-summary", dateFilter);
}

function getEmailsByCustomer(customerName, dateFilter) {
  return requestDateAware("/emails/by-customer", dateFilter, { customerName });
}

function getPriorityTypeSummary(dateFilter) {
  return requestDateAware("/emails/priority-type-summary", dateFilter);
}

function getEmailsByPriorityType(type, dateFilter) {
  return requestDateAware("/emails/by-priority-type", dateFilter, { type });
}

function getAllPrompts(agentId, dateFilter) {
  return requestDateAware("/agent-prompts", dateFilter, { agentId });
}

function getConfiguration(agentId, dateFilter) {
  return requestDateAware("/config", dateFilter, { agentId });
}

function displayStatus(value) {
  if (!value) return "";
  return String(value).toLowerCase() === "active" ? "Active" : "Inactive";
}

function getStatusTone(value) {
  const normalizedValue = String(value ?? "").trim().toLowerCase();
  if (normalizedValue === "active") return "active";
  if (normalizedValue === "inactive") return "inactive";
  return "neutral";
}

function renderStatusBadge(value, label = null) {
  const displayValue = label ?? displayStatus(value);
  if (!displayValue || displayValue === "-") return "-";
  return <span className={`status-badge ${getStatusTone(displayValue)}`}>{displayValue}</span>;
}

function getPriorityTone(value) {
  const normalizedValue = String(value ?? "").trim().toLowerCase();

  if (["critical", "high", "urgent", "1", "2"].includes(normalizedValue)) return "high";
  if (["medium", "moderate", "3"].includes(normalizedValue)) return "medium";
  if (["low", "normal", "4", "5"].includes(normalizedValue)) return "low";

  return "neutral";
}

function renderPriorityBadge(value) {
  const displayValue = String(value ?? "").trim();
  if (!displayValue) return "-";
  return <span className={`priority-badge ${getPriorityTone(displayValue)}`}>{displayValue}</span>;
}

function toAgentStatus(value) {
  return String(value).toLowerCase() === "inactive" ? "inactive" : "active";
}

function toApiStatus(value) {
  return String(value).toLowerCase();
}

const DISPLAY_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function parseBackendLocalDate(value) {
  if (value instanceof Date) {
    return value;
  }

  if (typeof value !== "string") {
    return null;
  }

  const trimmedValue = value.trim();
  if (/^\d{4}-\d{2}-\d{2}T/.test(trimmedValue) || /(?:Z|[+-]\d{2}:?\d{2})$/.test(trimmedValue)) {
    const parsedDate = new Date(trimmedValue);
    return Number.isNaN(parsedDate.getTime()) ? null : parsedDate;
  }

  const match = trimmedValue.match(
    /^(\d{4})-(\d{2})-(\d{2})(?:\s(\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,7})?)?)?/
  );

  if (!match) {
    return null;
  }

  const [, year, month, day, hour = "00", minute = "00", second = "00"] = match;
  return new Date(
    Number(year),
    Number(month) - 1,
    Number(day),
    Number(hour),
    Number(minute),
    Number(second)
  );
}

function formatDisplayDate(value) {
  const date = parseBackendLocalDate(value);
  if (!date || Number.isNaN(date.getTime())) return "-";
  return `${date.getDate()}-${DISPLAY_MONTHS[date.getMonth()]}-${date.getFullYear()}`;
}

function formatDisplayTime(value) {
  const date = parseBackendLocalDate(value);
  if (!date || Number.isNaN(date.getTime())) return "-";
  let hour = date.getHours();
  const minute = String(date.getMinutes()).padStart(2, "0");
  const period = hour >= 12 ? "pm" : "am";
  hour = hour % 12 || 12;
  return `${hour}:${minute} ${period}`;
}

function formatDisplayDateTime(value) {
  const date = parseBackendLocalDate(value);
  if (!date || Number.isNaN(date.getTime())) return "-";
  return `${formatDisplayDate(date)} ${formatDisplayTime(date)}`;
}

function formatDisplaySubject(value) {
  return String(value ?? "").trim() || "No Subject";
}

function getScrollableDetailTableClass(rowCount) {
  return rowCount > 5 ? " detail-table-scroll" : "";
}

function formatApiTimestamp(value, fallback = "-") {
  if (!value) return fallback;
  const formattedValue = formatDisplayDateTime(value);
  return formattedValue === "-" ? fallback : formattedValue;
}

function readFirstValue(record, keys) {
  for (const key of keys) {
    if (record?.[key]) return record[key];
  }
  return null;
}

function mapPromptRecord(prompt) {
  const createdValue = readFirstValue(prompt, ["created_at", "createdAt", "created_date", "createdDate"]);
  const updatedValue = readFirstValue(prompt, ["updated_at", "updatedAt", "updated_date", "updatedDate"]);

  return {
    id: prompt.id,
    name: prompt.prompt_name ?? "",
    text: prompt.prompt_text ?? "",
    status: displayStatus(prompt.status),
    createdDate: formatApiTimestamp(createdValue, "-"),
    updatedDate: formatApiTimestamp(updatedValue, "-"),
  };
}

function uniqueValues(values) {
  return Array.from(
    new Set(
      values
        .map((value) => (typeof value === "string" ? value.trim() : ""))
        .filter(Boolean),
    ),
  ).sort((first, second) => first.localeCompare(second));
}

function buildTeamsChannelName(organization, product) {
  const normalizedOrganization = (organization ?? "").trim();
  const normalizedProduct = (product ?? "").trim();
  return normalizedOrganization && normalizedProduct ? `KT - ${normalizedOrganization} - ${normalizedProduct}` : "";
}

function truncateWebhookUrl(value) {
  if (!value) return "-";
  if (value.length <= 28) return value;

  try {
    const url = new URL(value);
    const tail = url.pathname.split("/").filter(Boolean).pop() ?? url.hostname;
    return `${url.protocol}//.../${tail.slice(-12)}`;
  } catch {
    return `${value.slice(0, 12)}...${value.slice(-12)}`;
  }
}

function displayRoutingStatus(value) {
  return String(value).toUpperCase() === "ACTIVE" ? "Active" : "Inactive";
}

function toRoutingStatus(value) {
  return String(value).toLowerCase() === "active" || String(value).toUpperCase() === "ACTIVE"
    ? "ACTIVE"
    : "PENDING";
}

function App() {
  const [activeDateFilter, setActiveDateFilter] = useState(null);
  const calendarFilterValue = useMemo(
    () => ({
      activeDateFilter,
      applyDateFilter: setActiveDateFilter,
      clearDateFilter: () => setActiveDateFilter(null),
    }),
    [activeDateFilter],
  );

  return (
    <CalendarFilterContext.Provider value={calendarFilterValue}>
      <Router>
        <div className="app-shell">
          <Sidebar />
          <main className="main-pane">
            <HeaderIcons />
            <ActiveFilterBanner />
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/configuration" element={<Navigate to="/agents" replace />} />
              <Route path="/agents" element={<Agents />} />
              <Route path="/business-actions" element={<BusinessActions />} />
              <Route path="/employee-engagements" element={<EmployeeEngagements />} />
            </Routes>
          </main>
          <FilterPanel />
        </div>
      </Router>
    </CalendarFilterContext.Provider>
  );
}

function ActiveFilterBanner() {
  const { activeDateFilter, clearDateFilter } = useCalendarFilter();

  if (!hasActiveDateFilter(activeDateFilter)) {
    return null;
  }

  return (
    <div className="api-state">
      Showing records from {formatDisplayDate(activeDateFilter.startDate)} to {formatDisplayDate(activeDateFilter.endDate)}
      <button className="inline-retry-button" type="button" onClick={clearDateFilter}>
        Clear Filter
      </button>
    </div>
  );
}

function Sidebar() {
  const location = useLocation();
  const configurationActive = ["/agents", "/business-actions", "/employee-engagements"].includes(location.pathname);

  return (
    <aside className="sidebar">
      <div className="brand-block">
        <div className="logo-mark">A</div>
        <div>
          <div className="brand-name">AI</div>
          <div className="user-name">Agent tracker</div>
        </div>
      </div>
      <div className="sidebar-divider" />
      <nav className="nav-list" aria-label="Primary navigation">
        <NavLink to="/" className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}>
          <BriefcaseBusiness size={16} strokeWidth={1.8} />
          <span>Dashboard</span>
        </NavLink>
        <div className={`nav-group-header ${configurationActive ? "active" : ""}`}>
          <Settings size={16} strokeWidth={1.8} />
          <span>Configuration</span>
          <ChevronDown size={15} strokeWidth={1.8} />
        </div>
        <div className="nav-sublist">
          {navItems.slice(1).map((item) => {
            const Icon = item.icon;
            return (
              <NavLink key={item.path} to={item.path} className={({ isActive }) => `nav-link sub-link ${isActive ? "active" : ""}`}>
                <Icon size={15} strokeWidth={1.8} />
                <span>{item.label}</span>
              </NavLink>
            );
          })}
        </div>
      </nav>
    </aside>
  );
}

function HeaderIcons() {
  const icons = [
    ["Information", Info],
    ["Notification", Bell],
    ["Mail", Mail],
    ["Logout", LogOut],
  ];
  return (
    <header className="topbar">
      {icons.map(([label, Icon]) => (
        <button key={label} className="icon-button" aria-label={label} title={label}>
          <Icon size={18} strokeWidth={1.8} />
        </button>
      ))}
    </header>
  );
}

function PageTitle({ title, subtitle }) {
  return (
    <div className="page-title">
      <h1>{title}</h1>
      <p>{subtitle}</p>
    </div>
  );
}

function Dashboard() {
  const { activeDateFilter } = useCalendarFilter();
  const [dashboardAgents, setDashboardAgents] = useState([]);
  const [isLoadingDashboardAgents, setIsLoadingDashboardAgents] = useState(true);
  const [dashboardAgentsError, setDashboardAgentsError] = useState("");

  useEffect(() => {
    let isMounted = true;

    async function loadDashboardAgents() {
      try {
        setIsLoadingDashboardAgents(true);
        const agentsPayload = await listAgents(activeDateFilter);
        console.log("dashboard agents payload", agentsPayload);

        if (!isMounted) return;

        setDashboardAgents(Array.isArray(agentsPayload) ? agentsPayload : []);
        setDashboardAgentsError("");
      } catch (error) {
        console.error("[Dashboard] Failed to load agents", error);

        if (!isMounted) return;

        setDashboardAgents([]);
        setDashboardAgentsError(error instanceof Error ? error.message : "Failed to load dashboard agents");
      } finally {
        if (isMounted) {
          setIsLoadingDashboardAgents(false);
        }
      }
    }

    void loadDashboardAgents();

    return () => {
      isMounted = false;
    };
  }, [activeDateFilter]);

  const agentSummary = useMemo(() => {
    return dashboardAgents.reduce(
      (summary, agent) => {
        const status = String(agent.status ?? "").toLowerCase();
        if (status === "active") {
          summary.active += 1;
        }
        if (status === "inactive") {
          summary.inactive += 1;
        }
        return summary;
      },
      { active: 0, inactive: 0 },
    );
  }, [dashboardAgents]);

  return (
    <div className="page-content">
      <PageTitle title="Dashboard" subtitle="Check status of agents and the different business actions" />
      <section className="section">
        <SectionHeader title="Agents Summary" subtitle="Summary of Agents and Status" />
        {(isLoadingDashboardAgents || dashboardAgentsError) && (
          <div className={`api-state ${dashboardAgentsError ? "error" : ""}`}>
            {isLoadingDashboardAgents ? "Loading agents..." : dashboardAgentsError}
          </div>
        )}
        <div className="summary-card-row">
          <div className="small-summary">
            <div className="summary-label">Total Active Agents</div>
            <div className="summary-value">{agentSummary.active}</div>
          </div>
          <div className="small-summary">
            <div className="summary-label">Total Inactive Agents</div>
            <div className="summary-value">{agentSummary.inactive}</div>
          </div>
        </div>
      </section>
      <section className="section">
        <SectionHeader title="Business Actions Summary" subtitle="Status of agents and actions the agents are taking" />
        <Accordion />
      </section>
    </div>
  );
}

function SectionHeader({ title, subtitle }) {
  return (
    <div className="section-header">
      <h2>{title}</h2>
      <p>{subtitle}</p>
    </div>
  );
}

function Accordion() {
  const [open, setOpen] = useState("Emails Processed");
  const sections = [
    {
      title: "Emails Processed",
      summary: true,
    },
    {
      title: "Priority and Type",
      priorityTypes: true,
    },
  ];
  return (
    <div className="accordion">
      {sections.map((section) => (
        <div className="accordion-item" key={section.title}>
          <button className="accordion-trigger" onClick={() => setOpen(open === section.title ? "" : section.title)}>
            <span>{section.title}</span>
            <ChevronDown className={open === section.title ? "rotate" : ""} size={16} />
          </button>
          {open === section.title && (
            <div className="accordion-panel">
              {section.summary ? (
                <EmailSummaryRows />
              ) : section.priorityTypes ? (
                <PriorityTypeRows />
              ) : (
                section.lines.map((line) => <p key={line}>{line}</p>)
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function cleanPriorityTypeDisplay(value) {
  return String(value ?? "Unclassified")
    .replace(/^\[Support Intake\]\s*/i, "")
    .trim() || "Unclassified";
}

function PriorityTypeRows() {
  const { activeDateFilter } = useCalendarFilter();
  const filteredEmptyMessage = hasActiveDateFilter(activeDateFilter) ? FILTERED_EMPTY_MESSAGE : "No priority/type records found";
  const [expanded, setExpanded] = useState("");
  const [items, setItems] = useState([]);
  const [isLoadingSummary, setIsLoadingSummary] = useState(true);
  const [summaryError, setSummaryError] = useState("");
  const [detailEmailsByType, setDetailEmailsByType] = useState({});
  const [detailLoadingByType, setDetailLoadingByType] = useState({});
  const [detailErrorByType, setDetailErrorByType] = useState({});

  useEffect(() => {
    let isMounted = true;

    async function loadPriorityTypeSummary() {
      try {
        setIsLoadingSummary(true);
        const payload = await getPriorityTypeSummary(activeDateFilter);
        console.log("[Dashboard] priority-type summary payload", payload);

        if (!isMounted) return;

        setItems(Array.isArray(payload?.items) ? payload.items : []);
        setSummaryError("");
      } catch (error) {
        console.error("[Dashboard] Failed to load priority/type summary", error);

        if (!isMounted) return;

        setItems([]);
        setSummaryError("Unable to load priority/type summary");
      } finally {
        if (isMounted) {
          setIsLoadingSummary(false);
        }
      }
    }

    void loadPriorityTypeSummary();
    const interval = setInterval(() => {
      void loadPriorityTypeSummary();
    }, 30000);

    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, [activeDateFilter]);

  useEffect(() => {
    setExpanded("");
    setDetailEmailsByType({});
    setDetailLoadingByType({});
    setDetailErrorByType({});
  }, [activeDateFilter]);

  const loadPriorityTypeDetails = async (type) => {
    try {
      setDetailLoadingByType((current) => ({ ...current, [type]: true }));
      setDetailErrorByType((current) => ({ ...current, [type]: "" }));
      const payload = await getEmailsByPriorityType(type, activeDateFilter);
      console.log("[Dashboard] priority-type detail payload", payload);

      setDetailEmailsByType((current) => ({
        ...current,
        [type]: Array.isArray(payload?.emails) ? payload.emails : [],
      }));
    } catch (error) {
      console.error("[Dashboard] Failed to load priority/type details", error);
      setDetailEmailsByType((current) => ({ ...current, [type]: [] }));
      setDetailErrorByType((current) => ({ ...current, [type]: "Unable to load matching emails" }));
    } finally {
      setDetailLoadingByType((current) => ({ ...current, [type]: false }));
    }
  };

  const toggleDetails = (type) => {
    const nextExpanded = expanded === type ? "" : type;
    setExpanded(nextExpanded);

    if (nextExpanded && !detailEmailsByType[type] && !detailLoadingByType[type]) {
      void loadPriorityTypeDetails(type);
    }
  };

  if (isLoadingSummary) {
    return <div className="api-state">Loading priority/type summary...</div>;
  }

  if (summaryError) {
    return <div className="api-state error">{summaryError}</div>;
  }

  if (!items.length) {
    return <div className="api-state">{filteredEmptyMessage}</div>;
  }

  return (
    <div className="email-summary-list">
      {items.map((item) => {
        const type = item.type || "Unclassified";
        const isExpanded = expanded === type;
        return (
          <div className={`email-summary-item ${isExpanded ? "expanded" : ""}`} key={type}>
            <div className="email-summary-row">
              <strong>
                {cleanPriorityTypeDisplay(type)} - {toSafeDisplayCount(item.count)} Emails
              </strong>
              <button
                className="know-more-button"
                type="button"
                onClick={() => toggleDetails(type)}
              >
                {isExpanded ? "Collapse" : "Know More"}
              </button>
            </div>
            <div className="email-detail-wrap" aria-hidden={!isExpanded}>
              <PriorityTypeDetail
                emails={detailEmailsByType[type] ?? []}
                emptyMessage={filteredEmptyMessage}
                errorMessage={detailErrorByType[type] ?? ""}
                isLoading={Boolean(detailLoadingByType[type])}
                type={type}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function toSafeDisplayCount(value) {
  const count = Number(value ?? 0);
  return Number.isFinite(count) ? count : 0;
}

function PriorityTypeDetail({ emails, emptyMessage = "No emails found", errorMessage = "", isLoading = false, type = "" }) {
  const columns = ["Email ID", "Subject", "Issue Summary", "Sender Email", "Received Date", "Priority", "Status"];

  return (
    <div className="email-detail-panel">
      <div className={`table-wrap compact-detail-table priority-detail-table${getScrollableDetailTableClass(emails.length)}`}>
        <table>
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column}>{column}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={columns.length}>Loading emails...</td>
              </tr>
            ) : errorMessage ? (
              <tr>
                <td colSpan={columns.length}>{errorMessage}</td>
              </tr>
            ) : emails.length ? (
              emails.map((email, index) => {
                const senderEmail = email.senderEmail || "-";
                return (
                  <tr key={`${type}-${email.emailId ?? index}`}>
                    <td>{email.emailId ?? ""}</td>
                    <td>{formatDisplaySubject(email.displaySubject)}</td>
                    <td className="issue-summary-cell">{email.issueSummary || "No summary available"}</td>
                    <td>{String(senderEmail).includes("@") ? <a href={`mailto:${senderEmail}`}>{senderEmail}</a> : senderEmail}</td>
                    <td>{formatDisplayDate(email.receivedAt)}</td>
                    <td>{renderPriorityBadge(email.priority)}</td>
                    <td>{renderStatusBadge(email.status, email.status || "-")}</td>
                  </tr>
                );
              })
            ) : (
              <tr>
                <td colSpan={columns.length}>{emptyMessage}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const emailSummaryItems = [
  {
    id: "received",
    label: "Emails Received",
    columns: ["Email ID", "Subject", "Received Date", "Received Time"],
  },
  {
    id: "processed",
    label: "Emails Processed",
    columns: ["Email ID", "Subject", "Processed Date", "Processed Time"],
  },
  {
    id: "client",
    label: "Client Emails",
    columns: ["Email ID", "Subject", "Client Name", "Received Date"],
  },
];

function EmailSummaryRows() {
  const { activeDateFilter } = useCalendarFilter();
  const filteredEmptyMessage = hasActiveDateFilter(activeDateFilter) ? FILTERED_EMPTY_MESSAGE : "No emails found";
  const [expanded, setExpanded] = useState("");
  const [receivedEmails, setReceivedEmails] = useState([]);
  const [receivedEmailsCount, setReceivedEmailsCount] = useState(0);
  const [isLoadingReceivedEmails, setIsLoadingReceivedEmails] = useState(true);
  const [receivedEmailsError, setReceivedEmailsError] = useState("");
  const [processedEmails, setProcessedEmails] = useState([]);
  const [processedEmailsCount, setProcessedEmailsCount] = useState(0);
  const [isLoadingProcessedEmails, setIsLoadingProcessedEmails] = useState(true);
  const [processedEmailsError, setProcessedEmailsError] = useState("");
  const [clientCustomers, setClientCustomers] = useState([]);
  const [clientEmailsCount, setClientEmailsCount] = useState(0);
  const [isLoadingClientEmails, setIsLoadingClientEmails] = useState(true);
  const [clientEmailsError, setClientEmailsError] = useState("");
  const [selectedCustomerName, setSelectedCustomerName] = useState("");
  const [customerEmails, setCustomerEmails] = useState([]);
  const [isLoadingCustomerEmails, setIsLoadingCustomerEmails] = useState(false);
  const [customerEmailsError, setCustomerEmailsError] = useState("");

  useEffect(() => {
    let isMounted = true;

    async function loadReceivedEmails() {
      try {
        setIsLoadingReceivedEmails(true);
        const payload = await getReceivedEmails(activeDateFilter);

        if (!isMounted) return;

        setReceivedEmails(Array.isArray(payload?.emails) ? payload.emails : []);
        setReceivedEmailsCount(Number.isFinite(Number(payload?.count)) ? Number(payload.count) : 0);
        setReceivedEmailsError("");
      } catch (error) {
        console.error("[Dashboard] Failed to load received emails", error);

        if (!isMounted) return;

        setReceivedEmails([]);
        setReceivedEmailsCount(0);
        setReceivedEmailsError("Unable to load emails");
      } finally {
        if (isMounted) {
          setIsLoadingReceivedEmails(false);
        }
      }
    }

    void loadReceivedEmails();
    const interval = setInterval(() => {
      void loadReceivedEmails();
    }, 30000);

    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, [activeDateFilter]);

  useEffect(() => {
    let isMounted = true;

    async function loadProcessedEmails() {
      try {
        setIsLoadingProcessedEmails(true);
        const payload = await getProcessedEmails(activeDateFilter);

        if (!isMounted) return;

        setProcessedEmails(Array.isArray(payload?.emails) ? payload.emails : []);
        setProcessedEmailsCount(Number.isFinite(Number(payload?.count)) ? Number(payload.count) : 0);
        setProcessedEmailsError("");
      } catch (error) {
        console.error("[Dashboard] Failed to load processed emails", error);

        if (!isMounted) return;

        setProcessedEmails([]);
        setProcessedEmailsCount(0);
        setProcessedEmailsError("Unable to load emails");
      } finally {
        if (isMounted) {
          setIsLoadingProcessedEmails(false);
        }
      }
    }

    void loadProcessedEmails();
    const interval = setInterval(() => {
      void loadProcessedEmails();
    }, 30000);

    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, [activeDateFilter]);

  useEffect(() => {
    let isMounted = true;

    async function loadClientEmailSummary() {
      try {
        setIsLoadingClientEmails(true);
        const payload = await getClientEmailSummary(activeDateFilter);
        console.log("[Dashboard] client-summary payload", payload);

        if (!isMounted) return;

        setClientCustomers(Array.isArray(payload?.customers) ? payload.customers : []);
        setClientEmailsCount(Number.isFinite(Number(payload?.count)) ? Number(payload.count) : 0);
        setClientEmailsError("");
      } catch (error) {
        console.error("[Dashboard] Failed to load client email summary", error);

        if (!isMounted) return;

        setClientCustomers([]);
        setClientEmailsCount(0);
        setClientEmailsError("Unable to load emails");
      } finally {
        if (isMounted) {
          setIsLoadingClientEmails(false);
        }
      }
    }

    void loadClientEmailSummary();
    const interval = setInterval(() => {
      void loadClientEmailSummary();
    }, 30000);

    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, [activeDateFilter]);

  useEffect(() => {
    setSelectedCustomerName("");
    setCustomerEmails([]);
    setCustomerEmailsError("");
  }, [activeDateFilter]);

  const receivedEmailRows = useMemo(
    () =>
      receivedEmails.slice(0, 10).map((email) => ({
        cells: [
          email.emailId ?? "",
          formatDisplaySubject(email.displaySubject),
          formatDisplayDate(email.receivedDate),
          formatDisplayTime(email.receivedTime ?? email.receivedDate),
        ],
      })),
    [receivedEmails],
  );

  const processedEmailRows = useMemo(
    () =>
      processedEmails.slice(0, 10).map((email) => ({
        cells: [
          email.emailId ?? "",
          formatDisplaySubject(email.displaySubject),
          formatDisplayDate(email.processedDate),
          formatDisplayTime(email.processedTime ?? email.processedDate),
        ],
      })),
    [processedEmails],
  );

  const handleCustomerClick = async (customerName) => {
    try {
      setSelectedCustomerName(customerName);
      setCustomerEmails([]);
      setCustomerEmailsError("");
      setIsLoadingCustomerEmails(true);
      const payload = await getEmailsByCustomer(customerName, activeDateFilter);
      console.log("[Dashboard] by-customer payload", payload);

      setCustomerEmails(Array.isArray(payload?.emails) ? payload.emails : []);
      setCustomerEmailsError("");
    } catch (error) {
      console.error("[Dashboard] Failed to load customer emails", error);
      setCustomerEmails([]);
      setCustomerEmailsError("Unable to load customer emails");
    } finally {
      setIsLoadingCustomerEmails(false);
    }
  };

  const handleCustomerBack = () => {
    setSelectedCustomerName("");
    setCustomerEmails([]);
    setCustomerEmailsError("");
  };

  return (
    <div className="email-summary-list">
      {emailSummaryItems.map((item) => {
        const isExpanded = expanded === item.id;
        const isReceivedItem = item.id === "received";
        const isProcessedItem = item.id === "processed";
        const isClientItem = item.id === "client";
        const count = isReceivedItem
          ? receivedEmailsCount
          : isProcessedItem
            ? processedEmailsCount
            : isClientItem
              ? clientEmailsCount
              : item.count;
        const rows = isReceivedItem
          ? receivedEmailRows
          : isProcessedItem
            ? processedEmailRows
            : isClientItem
              ? []
              : item.rows;
        return (
          <div className={`email-summary-item ${isExpanded ? "expanded" : ""}`} key={item.id}>
            <div className="email-summary-row">
              <strong>
                {count} {item.label}
              </strong>
              <button
                className="know-more-button"
                type="button"
                onClick={() => {
                  setExpanded(isExpanded ? "" : item.id);
                  if (isClientItem) {
                    handleCustomerBack();
                  }
                }}
              >
                {isExpanded ? "Collapse" : "Know More"}
              </button>
            </div>
            <div className="email-detail-wrap" aria-hidden={!isExpanded}>
              {isClientItem ? (
                <ClientEmailDetail
                  customers={clientCustomers}
                  selectedCustomerName={selectedCustomerName}
                  emails={customerEmails}
                  isLoading={isLoadingClientEmails}
                  isLoadingEmails={isLoadingCustomerEmails}
                  errorMessage={clientEmailsError}
                  emailsErrorMessage={customerEmailsError}
                  emptyMessage={filteredEmptyMessage}
                  onCustomerClick={handleCustomerClick}
                  onBack={handleCustomerBack}
                />
              ) : (
                <EmailDetailTable
                  item={{ ...item, rows }}
                  emptyMessage={filteredEmptyMessage}
                  errorMessage={isReceivedItem ? receivedEmailsError : isProcessedItem ? processedEmailsError : ""}
                  isLoading={(isReceivedItem && isLoadingReceivedEmails) || (isProcessedItem && isLoadingProcessedEmails)}
                />
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ClientEmailDetail({
  customers,
  selectedCustomerName,
  emails,
  isLoading = false,
  isLoadingEmails = false,
  errorMessage = "",
  emailsErrorMessage = "",
  emptyMessage = "No emails found",
  onCustomerClick,
  onBack,
}) {
  if (selectedCustomerName) {
    return (
      <div className="email-detail-panel">
        <div className="client-detail-actions">
          <button className="secondary-button" type="button" onClick={onBack}>
            Back
          </button>
          <strong>{selectedCustomerName}</strong>
        </div>
        <div className={`table-wrap compact-detail-table email-summary-detail-table customer-email-table${getScrollableDetailTableClass(emails.length)}`}>
          <table>
            <thead>
              <tr>
                <th>Email ID</th>
                <th>Subject</th>
                <th>Issue Summary</th>
                <th>Sender Email</th>
                <th>Received Date</th>
                <th>Received Time</th>
                <th>Priority</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {isLoadingEmails ? (
                <tr>
                  <td colSpan={8}>Loading emails...</td>
                </tr>
              ) : emailsErrorMessage ? (
                <tr>
                  <td colSpan={8}>{emailsErrorMessage}</td>
                </tr>
              ) : emails.length ? (
                emails.map((email, index) => {
                  const senderEmail = email.senderEmail || "-";
                  return (
                    <tr key={`${selectedCustomerName}-${email.emailId ?? index}`}>
                      <td>{email.emailId ?? ""}</td>
                      <td>{formatDisplaySubject(email.displaySubject)}</td>
                      <td className="issue-summary-cell">{email.issueSummary || "No summary available"}</td>
                      <td>{String(senderEmail).includes("@") ? <a href={`mailto:${senderEmail}`}>{senderEmail}</a> : senderEmail}</td>
                      <td>{formatDisplayDate(email.receivedAt)}</td>
                      <td>{formatDisplayTime(email.receivedAt)}</td>
                      <td>{renderPriorityBadge(email.priority)}</td>
                      <td>{renderStatusBadge(email.status, email.status || "-")}</td>
                    </tr>
                  );
                })
              ) : (
                <tr>
                  <td colSpan={8}>{emptyMessage}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  return (
    <div className="email-detail-panel">
      <div className="table-wrap compact-detail-table email-summary-detail-table">
        <table>
          <thead>
            <tr>
              <th>Customer</th>
              <th>Total Client Emails</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={2}>Loading customers...</td>
              </tr>
            ) : errorMessage ? (
              <tr>
                <td colSpan={2}>{errorMessage}</td>
              </tr>
            ) : customers.length ? (
              customers.map((customer) => (
                <tr key={customer.customerName}>
                  <td>
                    <button
                      className="customer-link-button"
                      type="button"
                      onClick={() => onCustomerClick(customer.customerName)}
                    >
                      {customer.customerName}
                    </button>
                  </td>
                  <td>{customer.emailCount ?? 0}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={2}>{emptyMessage}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function EmailDetailTable({ item, emptyMessage = "No emails found", errorMessage = "", isLoading = false }) {
  return (
    <div className="email-detail-panel">
      <div className={`table-wrap compact-detail-table email-summary-detail-table${getScrollableDetailTableClass(item.rows.length)}`}>
        <table>
          <thead>
            <tr>
              {item.columns.map((column) => (
                <th key={column}>{column}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={item.columns.length}>Loading emails...</td>
              </tr>
            ) : errorMessage ? (
              <tr>
                <td colSpan={item.columns.length}>{errorMessage}</td>
              </tr>
            ) : item.rows.length ? (
              item.rows.map((row, index) => (
                <tr key={`${item.id}-${index}`}>
                  {row.cells.map((cell, cellIndex) => (
                    <td key={`${item.id}-${index}-${cellIndex}`}>{String(cell).includes("@") ? <a href={`mailto:${cell}`}>{cell}</a> : cell}</td>
                  ))}
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={item.columns.length}>{emptyMessage}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Agents() {
  const { activeDateFilter } = useCalendarFilter();
  const [agents, setAgents] = useState([]);
  const [isLoadingAgents, setIsLoadingAgents] = useState(true);
  const [agentsError, setAgentsError] = useState("");
  const [agentFormError, setAgentFormError] = useState("");
  const [isAddingAgent, setIsAddingAgent] = useState(false);
  const [isSavingAgent, setIsSavingAgent] = useState(false);
  const [editingAgent, setEditingAgent] = useState(null);
  const [agentDraft, setAgentDraft] = useState(DEFAULT_AGENT_DRAFT);
  const [agentSort, setAgentSort] = useState({ key: "", direction: "asc" });

  const refreshAgents = async () => {
    try {
      setIsLoadingAgents(true);
      const records = await listAgents(activeDateFilter);
      setAgents(Array.isArray(records) ? records : []);
      setAgentsError("");
    } catch (error) {
      console.error("[Agents] Failed to load agents", error);
      setAgents([]);
      setAgentsError(error instanceof Error ? error.message : "Failed to load agents");
    } finally {
      setIsLoadingAgents(false);
    }
  };

  useEffect(() => {
    void refreshAgents();
  }, [activeDateFilter]);

  const sortedAgents = useMemo(() => {
    if (!agentSort.key) return agents;

    return [...agents].sort((a, b) => {
      const first = new Date(a[agentSort.key]).getTime() || 0;
      const second = new Date(b[agentSort.key]).getTime() || 0;
      return agentSort.direction === "asc" ? first - second : second - first;
    });
  }, [agents, agentSort]);

  const shouldShowAgentsState = isLoadingAgents || (agentsError && agents.length === 0);

  const toggleAgentSort = (key) => {
    setAgentSort((current) => ({
      key,
      direction: current.key === key && current.direction === "asc" ? "desc" : "asc",
    }));
  };

  const editAgent = (agent) => {
    setEditingAgent(agent.id);
    setIsAddingAgent(false);
    setAgentDraft({
      title: agent.title ?? "",
      description: agent.description ?? "",
      status: toAgentStatus(agent.status),
    });
  };

  const resetAgentDraft = () => {
    setEditingAgent(null);
    setIsAddingAgent(false);
    setAgentDraft(DEFAULT_AGENT_DRAFT);
    setAgentFormError("");
  };

  const saveAgent = async (agentId = null) => {
    const payload = {
      title: agentDraft.title.trim(),
      description: agentDraft.description.trim(),
      status: toAgentStatus(agentDraft.status),
    };

    if (!payload.title) {
      setAgentFormError("Agent name is required.");
      return;
    }

    const normalizedTitle = payload.title.toLowerCase().trim();
    if (!agentId) {
      const duplicateAgent = agents.find(
        (a) => a.title.toLowerCase().trim() === normalizedTitle
      );
      if (duplicateAgent) {
        setAgentFormError("Agent name already exists.");
        return;
      }
    } else {
      const duplicateAgent = agents.find(
        (a) => a.id !== agentId && a.title.toLowerCase().trim() === normalizedTitle
      );
      if (duplicateAgent) {
        setAgentFormError("Agent name already exists.");
        return;
      }
    }

    try {
      setIsSavingAgent(true);
      setAgentsError("");
      setAgentFormError("");

      if (agentId) {
        await updateAgentRecord(agentId, payload);
      } else {
        await createAgent(payload);
      }

      await refreshAgents();
      resetAgentDraft();
    } catch (error) {
      console.error("[Agents] Failed to save agent", { agentId, payload, error });
      const message = error instanceof Error ? error.message : "Failed to save agent";
      setAgentFormError(message);
      setAgentsError(message);
    } finally {
      setIsSavingAgent(false);
    }
  };

  const cancelAgentEdit = () => {
    resetAgentDraft();
  };

  const toggleAgentStatus = async (agent) => {
    const nextStatus = agent.status === "active" ? "inactive" : "active";
    console.log("Updating agent status", agent.id, nextStatus);

    try {
      setIsSavingAgent(true);
      setAgentsError("");
      setAgentFormError("");
      await updateAgentRecord(agent.id, { status: nextStatus });
      await refreshAgents();
    } catch (error) {
      console.error("[Agents] Failed to update agent status", { agentId: agent.id, nextStatus, error });
      setAgentsError(error instanceof Error ? error.message : "Failed to update agent status");
    } finally {
      setIsSavingAgent(false);
    }
  };

  const deleteAgent = async (agent) => {
    if (!window.confirm("Deleting this agent will also delete all related configuration and email records. Continue?")) return;
    console.log("Deleting agent", agent.id);

    try {
      setIsSavingAgent(true);
      setAgentsError("");
      setAgentFormError("");
      await deleteAgentRecord(agent.id);
      await refreshAgents();
    } catch (error) {
      console.error("[Agents] Failed to delete agent", { agentId: agent.id, error });
      setAgentsError(error instanceof Error ? error.message : "Failed to delete agent");
    } finally {
      setIsSavingAgent(false);
    }
  };

  return (
    <div className="page-content">
      <PageTitle title="Agents" subtitle="This tracks the agents business tasks and its configuration" />
      <section className="section">
        <div className="mini-config-heading">
          <SectionHeader title="Basic Agent Configurations" subtitle="Setup configuration details for the Agents" />
          <button
            className="mini-add-button"
            type="button"
            onClick={() => {
              setIsAddingAgent(true);
              setEditingAgent(null);
              setAgentDraft(DEFAULT_AGENT_DRAFT);
              setAgentFormError("");
            }}
            disabled={isSavingAgent}
          >
            + Add
          </button>
        </div>
        {shouldShowAgentsState && (
          <div className={`api-state ${agentsError ? "error" : ""}`}>
            {isLoadingAgents ? "Loading agents..." : agentsError}
            {agentsError && (
              <button className="inline-retry-button" type="button" onClick={() => void refreshAgents()}>
                Retry
              </button>
            )}
          </div>
        )}
        <EnterpriseTable
          columns={[
            "Agent Name",
            "Description",
            "Status",
            {
              label: "Created Date",
              sorted: agentSort.key === "created_at" ? agentSort.direction : "",
              onSort: () => toggleAgentSort("created_at"),
            },
            {
              label: "Last Updated Date",
              sorted: agentSort.key === "updated_at" ? agentSort.direction : "",
              onSort: () => toggleAgentSort("updated_at"),
            },
            "Actions",
          ]}
          rows={sortedAgents.map((agent) => [
              editingAgent === agent.id ? (
                <input
                  className="inline-edit-input"
                  value={agentDraft.title}
                  onChange={(event) => setAgentDraft((draft) => ({ ...draft, title: event.target.value }))}
                />
              ) : (
                agent.title
              ),
              editingAgent === agent.id ? (
                <input
                  className="inline-edit-input"
                  value={agentDraft.description}
                  onChange={(event) => setAgentDraft((draft) => ({ ...draft, description: event.target.value }))}
                />
              ) : (
                agent.description || "-"
              ),
              editingAgent === agent.id ? (
                <select
                  className="inline-edit-input status-input"
                  value={agentDraft.status}
                  onChange={(event) => setAgentDraft((draft) => ({ ...draft, status: event.target.value }))}
                >
                  <option value="active">Active</option>
                  <option value="inactive">Inactive</option>
                </select>
              ) : (
                <button
                  className={`status-badge ${getStatusTone(agent.status)} status-toggle-button`}
                  type="button"
                  onClick={() => void toggleAgentStatus(agent)}
                  disabled={isSavingAgent}
                >
                  {displayStatus(agent.status)}
                </button>
              ),
              <span title={agent.created_at}>{formatDisplayDateTime(agent.created_at)}</span>,
              <span title={agent.updated_at}>{formatDisplayDateTime(agent.updated_at)}</span>,
              <RowActions
                key={agent.id}
                isEditing={editingAgent === agent.id}
                onEdit={() => editAgent(agent)}
                onSave={() => void saveAgent(agent.id)}
                onCancel={cancelAgentEdit}
                onDelete={() => void deleteAgent(agent)}
              />,
            ])}
        />
        {!isLoadingAgents && !agents.length && (
          <div className="empty-table-note">
            {hasActiveDateFilter(activeDateFilter) ? FILTERED_EMPTY_MESSAGE : "No agents are configured."}
          </div>
        )}
      </section>
      {isAddingAgent && (
        <div className="dialog-backdrop" role="presentation" onMouseDown={cancelAgentEdit}>
          <form
            className="confirm-dialog mini-add-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="add-agent-title"
            onSubmit={(event) => {
              event.preventDefault();
              void saveAgent();
            }}
            onMouseDown={(event) => event.stopPropagation()}
          >
            <h3 id="add-agent-title">Add Agent</h3>
            {agentFormError && <div className="api-state error">{agentFormError}</div>}
            <div className="mini-add-fields">
              <label className="form-field">
                <span>Agent Name</span>
                <input
                  value={agentDraft.title}
                  onChange={(event) => setAgentDraft((draft) => ({ ...draft, title: event.target.value }))}
                  autoFocus
                />
              </label>
              <label className="form-field">
                <span>Description</span>
                <input
                  value={agentDraft.description}
                  onChange={(event) => setAgentDraft((draft) => ({ ...draft, description: event.target.value }))}
                />
              </label>
              <label className="form-field">
                <span>Status</span>
                <select
                  value={agentDraft.status}
                  onChange={(event) => setAgentDraft((draft) => ({ ...draft, status: event.target.value }))}
                >
                  <option value="active">Active</option>
                  <option value="inactive">Inactive</option>
                </select>
              </label>
            </div>
            <div className="dialog-actions">
              <button className="dialog-cancel" type="button" onClick={cancelAgentEdit}>Cancel</button>
              <button className="prompt-primary-button" type="submit" disabled={isSavingAgent}>
                {isSavingAgent ? "Saving..." : "Save"}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}

function BusinessActions() {
  const { activeDateFilter } = useCalendarFilter();
  const [agents, setAgents] = useState([]);
  const [activeAgentId, setActiveAgentId] = useState("");
  const [configuration, setConfiguration] = useState(EMPTY_CONFIGURATION);
  const [isLoadingConfig, setIsLoadingConfig] = useState(true);
  const [configError, setConfigError] = useState("");

  useEffect(() => {
    let isMounted = true;

    async function loadAgentsForConfiguration() {
      try {
        const records = await listAgents();
        if (!isMounted) return;

        setAgents(records);
        const preferredAgent = records.find((agent) => agent.status === "active") ?? records[0];
        setActiveAgentId((current) =>
          records.some((agent) => String(agent.id) === current)
            ? current
            : preferredAgent
              ? String(preferredAgent.id)
              : ""
        );
      } catch (error) {
        if (!isMounted) return;
        setConfigError(error instanceof Error ? error.message : "Failed to load agents");
        setIsLoadingConfig(false);
      }
    }

    void loadAgentsForConfiguration();

    return () => {
      isMounted = false;
    };
  }, []);

  const refreshConfiguration = async (agentId = activeAgentId) => {
    if (!agentId) {
      setConfiguration(EMPTY_CONFIGURATION);
      setIsLoadingConfig(false);
      return;
    }

    try {
      setIsLoadingConfig(true);
      const data = await getConfiguration(Number(agentId), activeDateFilter);
      setConfiguration({
        ...EMPTY_CONFIGURATION,
        ...data,
        teamsRouteConfigs: data.teamsRouteConfigs ?? [],
      });
      setConfigError("");
    } catch (error) {
      setConfiguration(EMPTY_CONFIGURATION);
      setConfigError(error instanceof Error ? error.message : "Failed to load configuration");
    } finally {
      setIsLoadingConfig(false);
    }
  };

  useEffect(() => {
    void refreshConfiguration(activeAgentId);
  }, [activeAgentId, activeDateFilter]);

  const destinationOptions = useMemo(() => {
    const organizations = uniqueValues(configuration.destinationEmails.map((record) => record.organization));
    const productsByOrganization = configuration.destinationEmails.reduce((options, record) => {
      const organization = (record.organization ?? "").trim();
      const product = (record.product_name ?? "").trim();
      if (!organization || !product) return options;
      options[organization] = uniqueValues([...(options[organization] ?? []), product]);
      return options;
    }, {});
    const products = uniqueValues(configuration.destinationEmails.map((record) => record.product_name));

    return { organizations, products, productsByOrganization, records: configuration.destinationEmails };
  }, [configuration.destinationEmails]);

  const sourceEmailOptions = useMemo(
    () =>
      uniqueValues(
        configuration.sourceEmails
          .filter((record) => String(record.status ?? "").toLowerCase() === "active")
          .map((record) => record.email),
      ),
    [configuration.sourceEmails],
  );

  return (
    <div className="page-content">
      <div className="business-config-page">
        <PageTitle title="Business Actions" subtitle="This tracks the agents business tasks and its configuration" />
        <div className="config-toolbar">
          <label className="form-field">
            <span>Selected Agent</span>
            <select
              value={activeAgentId}
              onChange={(event) => setActiveAgentId(event.target.value)}
              disabled={!agents.length}
            >
              {!agents.length && <option value="">No agents available</option>}
              {agents.map((agent) => (
                <option key={agent.id} value={agent.id}>
                  {agent.title} ({displayStatus(agent.status)})
                </option>
              ))}
            </select>
          </label>
          {(isLoadingConfig || configError) && (
            <div className={`api-state ${configError ? "error" : ""}`}>
              {isLoadingConfig ? "Loading configuration..." : configError}
            </div>
          )}
        </div>
        <AgentPromptConfiguration agentId={Number(activeAgentId)} />
        <ConfigSection
          title="Email Configuration"
          subtitle="Configure the email accounts used for monitoring, processing, and managing customer communications."
        >
          <div className="config-table-row">
            <MiniConfigTable
              title="Source Emails"
              description="Enter the support or shared mailbox that receives customer emails and will be monitored by the system."
              variant="source"
              rows={configuration.sourceEmails}
              agentId={Number(activeAgentId)}
              onRefresh={refreshConfiguration}
              emptyMessage={hasActiveDateFilter(activeDateFilter) ? FILTERED_EMPTY_MESSAGE : "No records"}
            />
            <MiniConfigTable
              title="Customer Email List"
              description="Add customer email addresses and organizations so the system can identify clients and process their communications correctly."
              variant="destination"
              rows={configuration.destinationEmails}
              agentId={Number(activeAgentId)}
              onRefresh={refreshConfiguration}
              destinationOptions={destinationOptions}
              emptyMessage={hasActiveDateFilter(activeDateFilter) ? FILTERED_EMPTY_MESSAGE : "No records"}
            />
          </div>
        </ConfigSection>
        <ConfigSection
          title=""
          subtitle=""
        >
          <div className="config-table-row monitoring-config-row">
            <MiniConfigTable
              title="Watch Emails"
              description="Enter employee or team email addresses that should be monitored for customer communications and support requests."
              variant="watch"
              rows={configuration.watchEmails}
              agentId={Number(activeAgentId)}
              onRefresh={refreshConfiguration}
              emptyMessage={hasActiveDateFilter(activeDateFilter) ? FILTERED_EMPTY_MESSAGE : "No records"}
            />
            <MiniConfigTable
              title="Key Customers"
              description="Add important customers who require priority attention, faster response handling, or escalation monitoring."
              variant="keyCustomer"
              rows={configuration.keyCustomers}
              agentId={Number(activeAgentId)}
              onRefresh={refreshConfiguration}
              emptyMessage={hasActiveDateFilter(activeDateFilter) ? FILTERED_EMPTY_MESSAGE : "No records"}
            />
          </div>
        </ConfigSection>
        <ConfigSection
          title=""
          subtitle=""
        >
          <MiniConfigTable
            title="Route Configuration"
            description="Define where processed emails should be routed, such as Microsoft Teams channels, support groups, or business teams."
            variant="route"
            rows={configuration.teamsRouteConfigs}
            agentId={Number(activeAgentId)}
            onRefresh={refreshConfiguration}
            destinationOptions={destinationOptions}
            sourceEmailOptions={sourceEmailOptions}
            emptyMessage={hasActiveDateFilter(activeDateFilter) ? FILTERED_EMPTY_MESSAGE : "No records"}
          />
        </ConfigSection>
        <ConfigSection
          title=""
          subtitle=""
        >
          <MiniConfigTable
            title="Classification Rules"
            description="Create rules that automatically categorize emails based on keywords, customers, priorities, or business requirements."
            variant="classification"
            rows={configuration.classifications}
            agentId={Number(activeAgentId)}
            onRefresh={refreshConfiguration}
            emptyMessage={hasActiveDateFilter(activeDateFilter) ? FILTERED_EMPTY_MESSAGE : "No records"}
          />
        </ConfigSection>
      </div>
    </div>
  );
}

function AgentPromptConfiguration({ agentId }) {
  const { activeDateFilter } = useCalendarFilter();
  const [prompts, setPrompts] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [form, setForm] = useState({ id: null, name: "", text: "", status: "Active", createdDate: "", updatedDate: "" });
  const [isLoadingPrompts, setIsLoadingPrompts] = useState(true);
  const [isSavingPrompt, setIsSavingPrompt] = useState(false);
  const [isDeletingPrompt, setIsDeletingPrompt] = useState(false);
  const [errors, setErrors] = useState({});
  const [toast, setToast] = useState(null);
  const selectedPrompt = prompts.find((prompt) => prompt.id === selectedId);
  const isDirty = selectedPrompt
    ? ["name", "text", "status"].some((field) => form[field] !== selectedPrompt[field])
    : Boolean(form.text.trim() || form.name.trim());

  const refreshPrompts = async (preferredPromptId = selectedId) => {
    try {
      setIsLoadingPrompts(true);
      const records = await getAllPrompts(agentId, activeDateFilter);
      const mappedPrompts = records.map(mapPromptRecord);
      setPrompts(mappedPrompts);

      const nextSelected = preferredPromptId
        ? mappedPrompts.find((prompt) => prompt.id === preferredPromptId)
        : mappedPrompts.find((prompt) => prompt.status === "Active") ?? mappedPrompts[0];

      if (nextSelected) {
        setSelectedId(nextSelected.id);
        setForm({ ...nextSelected });
      } else {
        setSelectedId(null);
        setForm({ id: null, name: "", text: "", status: "Active", createdDate: "Not saved", updatedDate: "Not saved" });
      }
    } catch (error) {
      notify(error instanceof Error ? error.message : "Failed to load prompt configurations", "error");
    } finally {
      setIsLoadingPrompts(false);
    }
  };

  useEffect(() => {
    void refreshPrompts();
  }, [agentId, activeDateFilter]);

  const notify = (message, type = "success") => {
    setToast({ message, type });
    window.setTimeout(() => setToast(null), 3200);
  };

  const updateField = (field, value) => {
    setForm((current) => ({ ...current, [field]: value }));
    setErrors((current) => ({ ...current, [field]: "" }));
  };

  const editPrompt = (prompt) => {
    setSelectedId(prompt.id);
    setForm({ ...prompt });
    setErrors({});
    document.querySelector(".prompt-form-card")?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const startNewPrompt = () => {
    setSelectedId(null);
    setForm({ id: null, name: "", text: "", status: "Active", createdDate: "Not saved", updatedDate: "Not saved" });
    setErrors({});
    document.querySelector(".prompt-form-card")?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const updatePrompt = async (event) => {
    event.preventDefault();
    const nextErrors = {};
    if (!form.text.trim()) nextErrors.text = "Prompt text is required.";

    if (Object.keys(nextErrors).length) {
      setErrors(nextErrors);
      notify("Please complete the required fields.", "error");
      return;
    }

    try {
      setIsSavingPrompt(true);
      const payload = {
        prompt_name: form.name.trim() || null,
        prompt_text: form.text.trim(),
        status: toApiStatus(form.status),
      };

      let savedPrompt;
      if (selectedPrompt) {
        savedPrompt = await updatePromptRecord(selectedPrompt.id, payload);
      } else {
        savedPrompt = await savePrompt(payload);
      }

      await refreshPrompts(savedPrompt?.id ?? selectedPrompt?.id ?? null);
      notify("Prompt configuration saved successfully.");
    } catch (error) {
      notify(error instanceof Error ? error.message : "Failed to save prompt", "error");
    } finally {
      setIsSavingPrompt(false);
    }
  };

  const removePrompt = async (prompt) => {
    const promptLabel = prompt.name || `Prompt ${prompt.id}`;
    const confirmed = window.confirm(`Delete "${promptLabel}" permanently?`);

    if (!confirmed) {
      return;
    }

    try {
      setIsDeletingPrompt(true);
      await deletePrompt(prompt.id);
      await refreshPrompts(null);
      notify("Prompt deleted successfully.");
    } catch (error) {
      notify(error instanceof Error ? error.message : "Failed to delete prompt", "error");
    } finally {
      setIsDeletingPrompt(false);
    }
  };

  return (
    <ConfigSection
      title="Agent Prompt Configuration"
      subtitle="Configure the AI instructions used to analyze emails, generate summaries, determine priorities, and recommend actions."
      action={<button className="mini-add-button" type="button" onClick={startNewPrompt}>+ Add</button>}
    >
      <div className="prompt-config-card">
        <form className="prompt-form-card" onSubmit={updatePrompt} noValidate>
          {(isLoadingPrompts || !prompts.length) && (
            <div className="api-state">
              {isLoadingPrompts
                ? "Loading prompt configurations..."
                : hasActiveDateFilter(activeDateFilter)
                  ? FILTERED_EMPTY_MESSAGE
                  : "No prompt configurations available."}
            </div>
          )}
          <div className="prompt-form-grid">
            <label className="form-field prompt-name-field">
              <span>Prompt Name</span>
              <input
                type="text"
                value={form.name}
                onChange={(event) => updateField("name", event.target.value)}
                aria-invalid={Boolean(errors.name)}
                placeholder="Enter prompt name"
              />
              {errors.name && <small className="field-error">{errors.name}</small>}
            </label>
            <label className="form-field">
              <span>Status</span>
              <select value={form.status} onChange={(event) => updateField("status", event.target.value)}>
                <option>Active</option>
                <option>Inactive</option>
              </select>
            </label>
            <label className="form-field">
              <span>Created Date &amp; Time</span>
              <input type="text" value={form.createdDate} readOnly />
            </label>
            <label className="form-field">
              <span>Updated Date &amp; Time</span>
              <input type="text" value={form.updatedDate} readOnly />
            </label>
            <label className="form-field prompt-text-field">
              <span>Prompt Text</span>
              <textarea
                value={form.text}
                onChange={(event) => updateField("text", event.target.value)}
                aria-invalid={Boolean(errors.text)}
                placeholder="Enter the complete AI agent prompt"
                maxLength={5000}
              />
              <span className="textarea-meta">
                {errors.text ? <small className="field-error">{errors.text}</small> : <small>Supports multi-line prompt content</small>}
                <small>{form.text.length} / 5000 characters</small>
              </span>
            </label>
          </div>
          <div className="prompt-form-actions">
            <span>{form.status === "Active" ? "This prompt will be used by the agent at runtime." : "This prompt is not used at runtime."}</span>
            <div className="inline-button-group">
              <button className="prompt-primary-button" type="submit" disabled={!isDirty || isSavingPrompt}>
                {isSavingPrompt ? "Saving..." : selectedPrompt ? "Update Prompt" : "Create Prompt"}
              </button>
            </div>
          </div>
        </form>

        <div className="prompt-table-wrap">
          <table className="prompt-table">
            <thead>
              <tr>
                <th>Prompt Name</th>
                <th className="status-column">Status</th>
                <th>Created Date</th>
                <th>Updated Date</th>
                <th className="actions-column">Actions</th>
              </tr>
            </thead>
            <tbody>
              {prompts.length ? (
                prompts.map((prompt) => (
                  <tr className={prompt.id === selectedId ? "selected-prompt-row" : ""} key={prompt.id}>
                    <td className="prompt-name-cell">{prompt.name}</td>
                    <td className="status-cell">{renderStatusBadge(prompt.status, prompt.status)}</td>
                    <td>{prompt.createdDate}</td>
                    <td>{prompt.updatedDate}</td>
                    <td className="actions-cell">
                      <div className="prompt-row-actions">
                        <button type="button" onClick={() => editPrompt(prompt)}>
                          <Edit3 size={14} /> Edit
                        </button>
                        <button
                          className="delete-action"
                          type="button"
                          disabled={isDeletingPrompt || isSavingPrompt}
                          onClick={() => removePrompt(prompt)}
                        >
                          <Trash2 size={14} /> Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td className="empty-prompt-table" colSpan="5">
                    {hasActiveDateFilter(activeDateFilter) ? FILTERED_EMPTY_MESSAGE : "No prompt configurations available."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {toast && (
        <div className={`prompt-toast ${toast.type}`} role="status">
          {toast.type === "success" ? <Check size={16} /> : <Info size={16} />}
          <span>{toast.message}</span>
          <button type="button" aria-label="Dismiss notification" onClick={() => setToast(null)}><X size={14} /></button>
        </div>
      )}
    </ConfigSection>
  );
}

function ConfigSection({ title, subtitle, action, children }) {
  const hasHeader = Boolean(title || subtitle || action);

  return (
    <section className="config-section">
      {hasHeader && (
        <div className="config-section-header">
          <div>
            {title && <h2>{title}</h2>}
            {subtitle && <p>{subtitle}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

const configTableDefinitions = {
  source: {
    resource: "source-emails",
    layout: "longContent",
    agentScoped: true,
    addTitle: "Add Source Email",
    columns: [
      { key: "email", label: "Email ID", type: "email" },
      { key: "description", label: "Description", type: "text", required: false },
      { key: "status", label: "Status", type: "select", options: ["active", "inactive"], display: displayStatus },
    ],
  },
  destination: {
    resource: "destination-emails",
    layout: "longContent",
    agentScoped: true,
    addTitle: "Add Customer Emails",
    columns: [
      { key: "email", label: "Email ID", type: "email" },
      { key: "organization", label: "Organization", type: "organizationSelect" },
      { key: "product_name", label: "Product", type: "productSelect" },
      { key: "status", label: "Status", type: "select", options: ["active", "inactive"], display: displayStatus },
    ],
  },
  watch: {
    resource: "watch-emails",
    layout: "configuration",
    agentScoped: true,
    addTitle: "Add Watch Email",
    columns: [
      { key: "email", label: "Email ID", type: "email" },
      { key: "type", label: "Type", type: "select", options: ["Business Analyst", "Developer", "Testing", "UI/UX"] },
      { key: "status", label: "Status", type: "select", options: ["active", "inactive"], display: displayStatus },
    ],
  },
  route: {
    resource: "teams-route-config",
    layout: "longContent",
    agentScoped: false,
    addTitle: "Add Teams Route",
    columns: [
      { key: "source_email", label: "Source Email", type: "sourceEmailSelect" },
      { key: "teams_channel_name", label: "Generated Teams Channel", type: "generatedChannel" },
      { key: "webhook_url", label: "Webhook URL", type: "webhook", required: false },
      { key: "routing_status", label: "Status", type: "select", options: ["Active", "Inactive"], display: displayRoutingStatus },
    ],
    formColumns: [
      { key: "source_email", label: "Source Email", type: "sourceEmailSelect" },
      { key: "organization_name", label: "Organization", type: "routeOrganizationSelect" },
      { key: "product_name", label: "Product", type: "routeProductSelect" },
      { key: "teams_channel_name", label: "Generated Teams Channel", type: "generatedChannel" },
      { key: "webhook_url", label: "Webhook URL", type: "webhook", required: false },
      { key: "routing_status", label: "Status", type: "select", options: ["Active", "Inactive"], display: displayRoutingStatus },
    ],
  },
  keyCustomer: {
    resource: "key-customers",
    layout: "configuration",
    agentScoped: true,
    addTitle: "Add Customer",
    columns: [
      { key: "name", label: "	Organization", type: "text" },
      { key: "email", label: "Email ID", type: "email" },
      { key: "priority", label: "Priority", type: "select", options: ["High", "Medium", "Low"] },
    ],
  },
  classification: {
    resource: "classifications",
    layout: "configuration",
    agentScoped: true,
    addTitle: "Add Classification Rule",
    columns: [
      { key: "rule_name", label: "Rule Name", type: "text" },
      { key: "category", label: "Category", type: "text" },
      { key: "priority", label: "Priority", type: "select", options: ["1", "2", "3", "4", "5"] },
      { key: "status", label: "Status", type: "select", options: ["active", "inactive"], display: displayStatus },
    ],
  },
};

function MiniConfigTable({
  title,
  description,
  variant,
  rows = [],
  agentId,
  onRefresh,
  destinationOptions = { organizations: [], products: [], productsByOrganization: {}, records: [] },
  sourceEmailOptions = [],
  emptyMessage = "No records",
}) {
  const definition = configTableDefinitions[variant];
  const formColumns = definition.formColumns ?? definition.columns;
  const [editingRow, setEditingRow] = useState(null);
  const [draft, setDraft] = useState({});
  const [addingRecord, setAddingRecord] = useState(false);
  const [addDraft, setAddDraft] = useState({});
  const [addError, setAddError] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [localOrganizations, setLocalOrganizations] = useState([]);
  const [localProductsByOrganization, setLocalProductsByOrganization] = useState({});

  const organizationOptions = uniqueValues([...destinationOptions.organizations, ...localOrganizations]);
  const allProductOptions = uniqueValues([
    ...destinationOptions.products,
    ...Object.values(localProductsByOrganization).flat(),
  ]);

  const getProductOptions = (organization) => {
    const normalizedOrganization = (organization ?? "").trim();
    const destinationProducts = normalizedOrganization
      ? destinationOptions.productsByOrganization[normalizedOrganization] ?? []
      : destinationOptions.products;
    const localProducts = normalizedOrganization
      ? localProductsByOrganization[normalizedOrganization] ?? []
      : Object.values(localProductsByOrganization).flat();
    return uniqueValues([...destinationProducts, ...localProducts]);
  };

  const findDestinationEmail = (organization, product) => {
    const normalizedOrganization = (organization ?? "").trim().toLowerCase();
    const normalizedProduct = (product ?? "").trim().toLowerCase();
    return destinationOptions.records.find(
      (record) =>
        (record.organization ?? "").trim().toLowerCase() === normalizedOrganization &&
        (record.product_name ?? "").trim().toLowerCase() === normalizedProduct,
    );
  };

  const getExpectedTeamsChannel = (sourceDraft) =>
    buildTeamsChannelName(sourceDraft.organization_name, sourceDraft.product_name);

  const normalizeRouteDraft = (sourceDraft) => ({
    ...sourceDraft,
    teams_channel_name: getExpectedTeamsChannel(sourceDraft),
  });

  const createEmptyDraft = () => {
    const nextDraft = formColumns.reduce((current, column) => {
      if (column.type === "select") {
        current[column.key] = column.options[0];
      } else {
        current[column.key] = "";
      }
      return current;
    }, {});

    if (variant === "destination") {
      nextDraft.organization = organizationOptions[0] ?? "";
      nextDraft.product_name = getProductOptions(nextDraft.organization)[0] ?? allProductOptions[0] ?? "";
    }

    if (variant === "route") {
      nextDraft.source_email = sourceEmailOptions[0] ?? "";
      nextDraft.organization_name = organizationOptions[0] ?? "";
      nextDraft.product_name = getProductOptions(nextDraft.organization_name)[0] ?? "";
      nextDraft.teams_channel_name = getExpectedTeamsChannel(nextDraft);
      nextDraft.routing_status = "Active";
    }

    return nextDraft;
  };

  const normalizeDraftForEdit = (row) => {
    if (variant !== "route") return { ...row };

    return normalizeRouteDraft({
      ...row,
      source_email: row.source_email ?? "",
      organization_name: row.organization_name ?? "",
      product_name: row.product_name ?? "",
      routing_status: displayRoutingStatus(row.routing_status),
    });
  };

  const updateDraftField = (setActiveDraft, key, value) => {
    setAddError("");
    setActiveDraft((current) => {
      const nextDraft = { ...current, [key]: value };

      if (variant === "route" && key === "organization_name") {
        const nextProducts = getProductOptions(value);
        nextDraft.product_name = nextProducts.includes(nextDraft.product_name) ? nextDraft.product_name : nextProducts[0] ?? "";
        nextDraft.teams_channel_name = getExpectedTeamsChannel(nextDraft);
      }

      if (variant === "route" && key === "product_name") {
        nextDraft.teams_channel_name = getExpectedTeamsChannel(nextDraft);
      }

      if (variant === "destination" && key === "organization") {
        const nextProducts = getProductOptions(value);
        nextDraft.product_name = nextProducts.includes(nextDraft.product_name) ? nextDraft.product_name : nextProducts[0] ?? nextDraft.product_name;
      }

      return nextDraft;
    });
  };

  const addOption = (setActiveDraft, key, activeDraft) => {
    const label = key === "product_name" ? "product" : "organization";
    const value = window.prompt(`Add new ${label}`);
    const normalizedValue = value?.trim();
    if (!normalizedValue) return;

    if (key === "organization" || key === "organization_name") {
      setLocalOrganizations((current) => uniqueValues([...current, normalizedValue]));
      updateDraftField(setActiveDraft, key, normalizedValue);
      return;
    }

    const organizationKey = (activeDraft.organization ?? activeDraft.organization_name ?? "").trim();
    setLocalProductsByOrganization((current) => ({
      ...current,
      [organizationKey]: uniqueValues([...(current[organizationKey] ?? []), normalizedValue]),
    }));
    updateDraftField(setActiveDraft, key, normalizedValue);
  };

  const buildPayload = (sourceDraft) => {
    if (variant === "route") {
      const normalizedDraft = normalizeRouteDraft(sourceDraft);
      const organization = normalizedDraft.organization_name.trim();
      const product = normalizedDraft.product_name.trim();

      return {
        source_email: normalizedDraft.source_email.trim(),
        organization_name: organization,
        product_name: product,
        teams_channel_name: normalizedDraft.teams_channel_name,
        webhook_url: normalizedDraft.webhook_url?.trim() || null,
        routing_status: toRoutingStatus(normalizedDraft.routing_status),
      };
    }

    const payload = definition.columns.reduce((nextPayload, column) => {
      const value = sourceDraft[column.key];
      nextPayload[column.key] = typeof value === "string" ? value.trim() : value;
      return nextPayload;
    }, {});

    if (definition.agentScoped) {
      payload.agent_id = agentId;
    }

    return payload;
  };

  const validateDraft = (sourceDraft) => {
    if (definition.agentScoped && !agentId) {
      return "Select a persisted agent before saving configuration.";
    }

    const missingField = formColumns.some((column) => {
      if (column.required === false) return false;
      const value = sourceDraft[column.key];
      return typeof value === "string" ? !value.trim() : !value;
    });

    if (missingField) {
      return "Please complete all required fields.";
    }

    if (variant === "route" && !sourceEmailOptions.includes(sourceDraft.source_email)) {
      return "Select a source email from active Source Emails.";
    }

    if (variant === "route" && !findDestinationEmail(sourceDraft.organization_name, sourceDraft.product_name)) {
      return "Select an organization and product from saved Customer Emails records.";
    }

    if (variant === "route" && sourceDraft.teams_channel_name !== getExpectedTeamsChannel(sourceDraft)) {
      return "Generated Teams Channel must match the selected organization and product.";
    }

    return "";
  };

  const editRow = (row) => {
    if (variant === "route") {
      setAddDraft(normalizeDraftForEdit(row));
      setEditingRow(row.id);
      setAddingRecord(true);
      setAddError("");
      return;
    }

    setEditingRow(row.id);
    setDraft(normalizeDraftForEdit(row));
  };

  const saveRow = async (id) => {
    const validationError = validateDraft(draft);
    if (validationError) {
      setAddError(validationError);
      return;
    }

    try {
      setIsSaving(true);
      setAddError("");
      const payload = buildPayload({ ...draft, id });
      if (variant === "route") {
        console.log("teams route payload", payload);
      }
      await updateConfigRecord(definition.resource, id, payload);
      await onRefresh();
      setEditingRow(null);
      setDraft({});
    } catch (error) {
      setAddError(error instanceof Error ? error.message : "Failed to save record");
    } finally {
      setIsSaving(false);
    }
  };

  const cancelRowEdit = () => {
    setEditingRow(null);
    setDraft({});
  };

  const deleteRow = async (id) => {
    if (!window.confirm("Delete this record?")) return;

    try {
      setIsSaving(true);
      setAddError("");
      await deleteConfigRecord(definition.resource, id);
      await onRefresh();
    } catch (error) {
      setAddError(error instanceof Error ? error.message : "Failed to delete record");
    } finally {
      setIsSaving(false);
    }
  };

  const openAddDialog = () => {
    setAddDraft(createEmptyDraft());
    setAddingRecord(true);
    setAddError("");
  };

  const closeAddDialog = () => {
    setAddingRecord(false);
    setAddDraft({});
    setEditingRow(null);
    setAddError("");
  };

  const addRecord = async (event) => {
    event.preventDefault();
    const validationError = validateDraft(addDraft);
    if (validationError) {
      setAddError(validationError);
      return;
    }

    try {
      setIsSaving(true);
      setAddError("");
      const payload = buildPayload(addDraft);
      if (variant === "route" && editingRow) {
        const updatePayload = buildPayload({ ...addDraft, id: editingRow });
        console.log("teams route payload", updatePayload);
        await updateConfigRecord(definition.resource, editingRow, updatePayload);
      } else {
        if (variant === "route") {
          console.log("teams route payload", payload);
        }
        await createConfigRecord(definition.resource, payload);
      }
      await onRefresh();
      closeAddDialog();
    } catch (error) {
      setAddError(error instanceof Error ? error.message : "Failed to add record");
    } finally {
      setIsSaving(false);
    }
  };

  const renderReadValue = (row, column) => {
    const value = column.type === "generatedChannel"
      ? buildTeamsChannelName(row.organization_name, row.product_name)
      : row[column.key];

    if (value === null || value === undefined || value === "") {
      return "-";
    }
    if (column.type === "webhook") {
      return <span className="webhook-link" title={value}>{truncateWebhookUrl(value)}</span>;
    }
    if (column.key === "status" || column.key === "routing_status") {
      const statusValue = column.display ? column.display(value) : value;
      return renderStatusBadge(statusValue, statusValue);
    }
    if (column.key === "priority") {
      return renderPriorityBadge(value);
    }
    if (column.display) {
      const displayValue = column.display(value);
      return <span className="cell-text" title={displayValue}>{displayValue}</span>;
    }
    if (column.type === "email") {
      return <a href={`mailto:${value}`} title={value}>{value}</a>;
    }
    return <span className="cell-text" title={value}>{value}</span>;
  };

  const renderSelectWithAdd = (activeDraft, setActiveDraft, column, options, autoFocus = false) => (
    <div className="select-add-row">
      <select
        className="inline-edit-input"
        value={activeDraft[column.key] ?? ""}
        onChange={(event) => updateDraftField(setActiveDraft, column.key, event.target.value)}
        autoFocus={autoFocus}
      >
        <option value="">Select</option>
        {options.map((option) => (
          <option key={option} value={option}>{option}</option>
        ))}
      </select>
      <button className="mini-add-button compact-add" type="button" onClick={() => addOption(setActiveDraft, column.key, activeDraft)}>+</button>
    </div>
  );

  const renderPlainSelect = (activeDraft, setActiveDraft, column, options, autoFocus = false) => (
    <select
      className="inline-edit-input"
      value={activeDraft[column.key] ?? ""}
      onChange={(event) => updateDraftField(setActiveDraft, column.key, event.target.value)}
      autoFocus={autoFocus}
    >
      <option value="">Select</option>
      {options.map((option) => (
        <option key={option} value={option}>{option}</option>
      ))}
    </select>
  );

  const renderEditor = (activeDraft, setActiveDraft, column, autoFocus = false) => {
    if (column.type === "organizationSelect") {
      return renderSelectWithAdd(activeDraft, setActiveDraft, column, organizationOptions, autoFocus);
    }

    if (column.type === "routeOrganizationSelect") {
      return renderPlainSelect(activeDraft, setActiveDraft, column, destinationOptions.organizations, autoFocus);
    }

    if (column.type === "sourceEmailSelect") {
      return renderPlainSelect(activeDraft, setActiveDraft, column, sourceEmailOptions, autoFocus);
    }

    if (column.type === "productSelect") {
      const organization = activeDraft.organization ?? activeDraft.organization_name;
      const options = getProductOptions(organization);
      return renderSelectWithAdd(activeDraft, setActiveDraft, column, options.length ? options : allProductOptions, autoFocus);
    }

    if (column.type === "routeProductSelect") {
      const options = getProductOptions(activeDraft.organization_name);
      return renderPlainSelect(activeDraft, setActiveDraft, column, options, autoFocus);
    }

    if (column.type === "generatedChannel") {
      const normalizedDraft = normalizeRouteDraft(activeDraft);
      return (
        <input
          className="inline-edit-input"
          value={normalizedDraft.teams_channel_name}
          readOnly
        />
      );
    }

    if (column.type === "select") {
      return (
        <select
          className={`inline-edit-input ${column.key === "status" || column.key === "routing_status" ? "status-input" : ""}`}
          value={activeDraft[column.key] ?? column.options[0]}
          onChange={(event) => updateDraftField(setActiveDraft, column.key, event.target.value)}
          autoFocus={autoFocus}
        >
          {column.options.map((option) => (
            <option key={option} value={option}>{option}</option>
          ))}
        </select>
      );
    }

    return (
      <input
        className="inline-edit-input"
        type={column.type === "email" ? "email" : "text"}
        value={activeDraft[column.key] ?? ""}
        onChange={(event) => updateDraftField(setActiveDraft, column.key, event.target.value)}
        autoFocus={autoFocus}
      />
    );
  };

  const getColumnClassName = (column, columnIndex = 0) => {
    const classes = [];

    if (columnIndex === 0) classes.push("primary-cell");
    if (columnIndex === 1) classes.push("secondary-cell");
    if (columnIndex === 2) classes.push("tertiary-cell");
    if (column.key === "status" || column.key === "routing_status") classes.push("status-cell");
    if (column.key === "priority") classes.push("priority-cell");
    if (column.key === "type") classes.push("type-cell");
    if (
      column.type === "email" ||
      column.type === "webhook" ||
      column.type === "generatedChannel" ||
      ["email_address", "source_email", "subject", "issue_summary", "description"].includes(column.key)
    ) {
      classes.push("long-text-cell");
    }

    return classes.join(" ");
  };

  const tableLayoutClass = definition.layout === "longContent" ? "long-content-table" : "configuration-table";

  return (
    <div className={`mini-config-block ${variant === "route" ? "route-config-block" : ""}`}>
      <div className="mini-config-heading">
        <div>
          <h3>{title}</h3>
          {description && <p className="mini-config-description">{description}</p>}
        </div>
      </div>
      <div className="mini-config-action-row">
        <button
          className="mini-add-button"
          type="button"
          onClick={openAddDialog}
          disabled={isSaving || (definition.agentScoped && !agentId) || (variant === "route" && (!destinationOptions.records.length || !sourceEmailOptions.length))}
        >
          + Add
        </button>
      </div>
      {addError && <div className="api-state error">{addError}</div>}
      <div className={`mini-config-table-wrap ${rows.length > 5 ? "scrollable" : ""}`.trim()}>
        <table className={`mini-config-table ${tableLayoutClass} ${variant === "route" ? "route-config-table" : ""}`}>
          <thead>
            <tr>
              {definition.columns.map((column, columnIndex) => (
                <th key={column.key} className={getColumnClassName(column, columnIndex)}>{column.label}</th>
              ))}
              <th className="actions-cell">Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.length ? (
              rows.map((row) => (
                <tr key={`${title}-${row.id}`}>
                  {definition.columns.map((column, columnIndex) => (
                    <td key={column.key} className={`${getColumnClassName(column, columnIndex)} ${column.type === "webhook" ? "webhook-cell" : ""}`.trim()}>
                      {editingRow === row.id ? renderEditor(draft, setDraft, column, columnIndex === 0) : renderReadValue(row, column)}
                    </td>
                  ))}
                  <td className="actions-cell">
                    <RowActions
                      isEditing={editingRow === row.id}
                      onEdit={() => editRow(row)}
                      onSave={() => void saveRow(row.id)}
                      onCancel={cancelRowEdit}
                      onDelete={() => void deleteRow(row.id)}
                    />
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={definition.columns.length + 1}>{emptyMessage}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {addingRecord && (
        <div className="dialog-backdrop" role="presentation" onMouseDown={closeAddDialog}>
          <form
            className="confirm-dialog mini-add-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby={`${title.replace(/\s+/g, "-").toLowerCase()}-add-title`}
            onSubmit={addRecord}
            onMouseDown={(event) => event.stopPropagation()}
          >
            <h3 id={`${title.replace(/\s+/g, "-").toLowerCase()}-add-title`}>
              {editingRow && variant === "route" ? "Edit Teams Route" : definition.addTitle}
            </h3>
            <div className="mini-add-fields">
              {formColumns.map((column, columnIndex) => (
                <label className="form-field" key={column.key}>
                  <span>{column.label}</span>
                  {renderEditor(addDraft, setAddDraft, column, columnIndex === 0)}
                </label>
              ))}
              {addError && <small className="field-error">{addError}</small>}
            </div>
            <div className="dialog-actions">
              <button className="dialog-cancel" type="button" onClick={closeAddDialog}>Cancel</button>
              <button className="prompt-primary-button" type="submit" disabled={isSaving}>Save</button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}

function EmployeeEngagements() {
  return (
    <div className="page-content">
      <PageTitle title="Employee Engagements" subtitle="Simple employee engagement records" />
      <section className="section">
        <EnterpriseTable columns={["Employee", "Department", "Tasks Assigned", "Tasks Completed", "Status"]} rows={engagementRows} />
      </section>
    </div>
  );
}

function RowActions({ isEditing = false, onEdit, onSave, onCancel, onDelete }) {
  if (isEditing) {
    return (
      <div className="row-actions">
        <button type="button" aria-label="Save" title="Save" onClick={onSave}>
          <Check size={15} />
        </button>
        <span aria-hidden="true">|</span>
        <button type="button" aria-label="Cancel" title="Cancel" onClick={onCancel}>
          <X size={15} />
        </button>
      </div>
    );
  }

  return (
    <div className="row-actions">
      <button type="button" aria-label="Edit" title="Edit" onClick={onEdit}>
        <Edit3 size={15} />
      </button>
      <span aria-hidden="true">|</span>
      <button type="button" aria-label="Delete" title="Delete" onClick={onDelete}>
        <Trash2 size={15} />
      </button>
    </div>
  );
}

function EnterpriseTable({ columns, rows }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((column) => {
              if (typeof column === "string") {
                return <th key={column}>{column}</th>;
              }

              return (
                <th key={column.label}>
                  <button className="sortable-heading" type="button" onClick={column.onSort}>
                    <span>{column.label}</span>
                    <span aria-hidden="true">{column.sorted === "asc" ? "↑" : column.sorted === "desc" ? "↓" : "↕"}</span>
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {row.map((cell, cellIndex) => (
                <td key={`${index}-${cellIndex}`}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FilterPanel() {
  const { activeDateFilter, applyDateFilter, clearDateFilter } = useCalendarFilter();
  const today = useMemo(() => startOfDay(new Date()), []);
  const [viewDate, setViewDate] = useState(new Date(today.getFullYear(), today.getMonth(), 1));
  const [selectedQuick, setSelectedQuick] = useState("This Month");
  const [draftRange, setDraftRange] = useState(() => getQuickRange("This Month", today));
  const monthLabel = viewDate.toLocaleString("en-US", { month: "long", year: "numeric" });
  const weeks = useMemo(() => buildMonth(viewDate), [viewDate]);
  const hasActiveFilter = hasActiveDateFilter(activeDateFilter);

  const moveMonth = (offset) => {
    setViewDate(new Date(viewDate.getFullYear(), viewDate.getMonth() + offset, 1));
  };

  const selectQuickFilter = (label) => {
    setSelectedQuick(label);

    if (label !== "Custom Range") {
      const nextRange = getQuickRange(label, today);
      setDraftRange(nextRange);
      setViewDate(new Date(nextRange.startDate.getFullYear(), nextRange.startDate.getMonth(), 1));
      return;
    }

    setDraftRange({ startDate: null, endDate: null });
  };

  const selectCalendarDate = (date) => {
    const selectedDate = startOfDay(date);
    setSelectedQuick("Custom Range");

    setDraftRange((current) => {
      if (selectedQuick !== "Custom Range" || !current.startDate || current.endDate) {
        return { startDate: selectedDate, endDate: null };
      }

      return selectedDate < current.startDate
        ? { startDate: selectedDate, endDate: current.startDate }
        : { startDate: current.startDate, endDate: selectedDate };
    });
  };

  const applyFilter = () => {
    if (!draftRange.startDate) {
      return;
    }

    applyDateFilter({
      startDate: toDateInputValue(draftRange.startDate),
      endDate: toDateInputValue(draftRange.endDate ?? draftRange.startDate),
    });
  };

  const clearFilter = () => {
    clearDateFilter();
    setSelectedQuick("This Month");
    const nextRange = getQuickRange("This Month", today);
    setDraftRange(nextRange);
    setViewDate(new Date(today.getFullYear(), today.getMonth(), 1));
  };

  return (
    <aside className="filter-panel">
      <div className="filter-title">
        <CalendarDays size={17} />
        <span>Calendar Filter</span>
      </div>
      <div className="calendar-box">
        <div className="calendar-head">
          <button className="icon-button small" onClick={() => moveMonth(-1)} aria-label="Previous month">
            <ChevronLeft size={17} />
          </button>
          <strong>{monthLabel}</strong>
          <button className="icon-button small" onClick={() => moveMonth(1)} aria-label="Next month">
            <ChevronRight size={17} />
          </button>
        </div>
        <div className="calendar-grid calendar-weekdays">
          {["S", "M", "T", "W", "T", "F", "S"].map((day) => (
            <span key={day}>{day}</span>
          ))}
        </div>
        <div className="calendar-grid">
          {weeks.flat().map((date, index) => {
            const inMonth = date.getMonth() === viewDate.getMonth();
            const isSelected = isDateInRange(date, draftRange);
            const isToday = sameDay(date, today);
            return (
              <button
                key={`${date.toISOString()}-${index}`}
                className={`date-cell ${inMonth ? "" : "muted"} ${isToday ? "today" : ""} ${isSelected ? "selected" : ""}`}
                onClick={() => selectCalendarDate(date)}
              >
                {date.getDate()}
              </button>
            );
          })}
        </div>
      </div>
      <div className="quick-filters">
        {["Today", "Yesterday", "This Week", "Last 7 Days", "This Month", "Last 30 Days", "Custom Range"].map((item) => (
          <label key={item}>
            <input type="radio" name="quick-date" checked={selectedQuick === item} onChange={() => selectQuickFilter(item)} />
            <span>{item}</span>
          </label>
        ))}
      </div>
      <div className="selected-card">
        <span>Selected Range</span>
        <strong>
          {draftRange.startDate
            ? `${formatDisplayDate(draftRange.startDate)} to ${formatDisplayDate(draftRange.endDate ?? draftRange.startDate)}`
            : "Select custom dates"}
        </strong>
        <button className="primary-button" type="button" onClick={applyFilter} disabled={!draftRange.startDate}>Apply Filter</button>
        <button className="secondary-button" type="button" onClick={clearFilter} disabled={!hasActiveFilter}>Clear Filter</button>
      </div>
    </aside>
  );
}

function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function addDays(date, days) {
  const nextDate = startOfDay(date);
  nextDate.setDate(nextDate.getDate() + days);
  return nextDate;
}

function toDateInputValue(date) {
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, "0"),
    String(date.getDate()).padStart(2, "0"),
  ].join("-");
}

function getQuickRange(label, today) {
  const currentDay = startOfDay(today);

  if (label === "Today") {
    return { startDate: currentDay, endDate: currentDay };
  }

  if (label === "Yesterday") {
    const yesterday = addDays(currentDay, -1);
    return { startDate: yesterday, endDate: yesterday };
  }

  if (label === "This Week") {
    return { startDate: addDays(currentDay, -currentDay.getDay()), endDate: currentDay };
  }

  if (label === "Last 7 Days") {
    return { startDate: addDays(currentDay, -6), endDate: currentDay };
  }

  if (label === "Last 30 Days") {
    return { startDate: addDays(currentDay, -29), endDate: currentDay };
  }

  return {
    startDate: new Date(currentDay.getFullYear(), currentDay.getMonth(), 1),
    endDate: currentDay,
  };
}

function buildMonth(date) {
  const start = new Date(date.getFullYear(), date.getMonth(), 1);
  const cursor = new Date(start);
  cursor.setDate(cursor.getDate() - cursor.getDay());
  return Array.from({ length: 6 }, () =>
    Array.from({ length: 7 }, () => {
      const current = new Date(cursor);
      cursor.setDate(cursor.getDate() + 1);
      return current;
    }),
  );
}

function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function isDateInRange(date, range) {
  if (!range?.startDate) {
    return false;
  }

  const currentDate = startOfDay(date).getTime();
  const endDate = range.endDate ?? range.startDate;
  return currentDate >= startOfDay(range.startDate).getTime() && currentDate <= startOfDay(endDate).getTime();
}

createRoot(document.getElementById("root")).render(<App />);
