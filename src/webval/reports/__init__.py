"""Report generation: Excel traceability matrix, HTML dashboard, JSON results."""

from webval.reports.excel import write_excel_report
from webval.reports.html import write_html_report
from webval.reports.json_out import write_json_results

__all__ = ["write_excel_report", "write_html_report", "write_json_results"]
