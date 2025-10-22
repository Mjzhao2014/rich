from rich.console import Console, ConsoleOptions, RenderResult
from rich.text import Text
from rich.style import Style
from rich.tree import Tree
from rich.panel import Panel
from rich import box
from typing import Dict, Optional, List
import time

class ServiceStatus:
    """Status of a service with nested dependencies."""
    def __init__(self, name: str):
        self.name = name
        self.status = "unknown"
        self.dependencies: List['ServiceStatus'] = []
        self.style = "white"
    
    def add_dependency(self, dependency: 'ServiceStatus') -> None:
        self.dependencies.append(dependency)
    
    def update_status(self, status: str) -> None:
        self.status = status

class StatusTree:
    """Rich renderable for displaying service status hierarchy."""
    def __init__(self):
        self.root_services: List[ServiceStatus] = []
        self.status_styles = {
            "healthy": "green",
            "degraded": "yellow",
            "failed": "red"
        }
    
    def add_service(self, service: ServiceStatus) -> None:
        self.root_services.append(service)
    
    def _get_service_style(self, service: ServiceStatus) -> Style:
        base_style = self.status_styles.get(service.status, "white")
        return Style.parse(base_style)
    
    def _build_tree(self, service: ServiceStatus, tree: Tree) -> None:
        service_text = Text(f"{service.name}: {service.status}")
        service_text.style = self._get_service_style(service)
        
        branch = tree.add(service_text)
        for dep in service.dependencies:
            self._build_tree(dep, branch)
    
    def __rich_console__(
        self, 
        console: Console, 
        options: ConsoleOptions
    ) -> RenderResult:
        tree = Tree("Services Status")
        for service in self.root_services:
            self._build_tree(service, tree)
        
        panel = Panel(
            tree,
            title="System Status Dashboard",
            style="bold",
            box=box.ROUNDED
        )
        yield panel

def main():
    # Create service hierarchy
    database = ServiceStatus("Database")
    cache = ServiceStatus("Cache")
    api = ServiceStatus("API")
    web = ServiceStatus("Web")
    
    # Set up dependencies
    api.add_dependency(database)
    api.add_dependency(cache)
    web.add_dependency(api)
    
    # Create status tree
    status_tree = StatusTree()
    status_tree.add_service(web)
    
    console = Console()
    
    # Simulate status updates
    statuses = [
        ("Database", "healthy"),
        ("Cache", "degraded"),
        ("API", "degraded"),
        ("Web", "failed")
    ]
    
    for service_name, status in statuses:
        for service in [web, api, database, cache]:
            if service.name == service_name:
                service.update_status(status)
                console.print(status_tree)
                time.sleep(1)

if __name__ == "__main__":
    main()