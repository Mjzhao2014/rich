import hashlib
import json
from collections import OrderedDict
from abc import ABC, abstractmethod
from itertools import islice
from operator import itemgetter
from threading import RLock
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Iterable,
    List,
    NamedTuple,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)

from ._ratio import ratio_resolve
from .align import Align
from .console import Console, ConsoleOptions, RenderableType, RenderResult
from .highlighter import ReprHighlighter
from .panel import Panel
from .pretty import Pretty
from .region import Region
from .repr import Result, rich_repr
from .segment import Segment
from .style import StyleType

if TYPE_CHECKING:
    from rich.tree import Tree


class LayoutRender(NamedTuple):
    """An individual layout render."""

    region: Region
    render: List[List[Segment]]


RegionMap = Dict["Layout", Region]
RenderMap = Dict["Layout", LayoutRender]


class LayoutError(Exception):
    """Layout related error."""


class NoSplitter(LayoutError):
    """Requested splitter does not exist."""


class _Placeholder:
    """An internal renderable used as a Layout placeholder."""

    highlighter = ReprHighlighter()

    def __init__(self, layout: "Layout", style: StyleType = "") -> None:
        self.layout = layout
        self.style = style

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        width = options.max_width
        height = options.height or options.size.height
        layout = self.layout
        title = (
            f"{layout.name!r} ({width} x {height})"
            if layout.name
            else f"({width} x {height})"
        )
        yield Panel(
            Align.center(Pretty(layout), vertical="middle"),
            style=self.style,
            title=self.highlighter(title),
            border_style="blue",
            height=height,
        )


class Splitter(ABC):
    """Base class for a splitter."""

    name: str = ""

    @abstractmethod
    def get_tree_icon(self) -> str:
        """Get the icon (emoji) used in layout.tree"""

    @abstractmethod
    def divide(
        self, children: Sequence["Layout"], region: Region
    ) -> Iterable[Tuple["Layout", Region]]:
        """Divide a region amongst several child layouts.

        Args:
            children (Sequence(Layout)): A number of child layouts.
            region (Region): A rectangular region to divide.
        """


class RowSplitter(Splitter):
    """Split a layout region in to rows."""

    name = "row"

    def get_tree_icon(self) -> str:
        return "[layout.tree.row]⬌"

    def divide(
        self, children: Sequence["Layout"], region: Region
    ) -> Iterable[Tuple["Layout", Region]]:
        x, y, width, height = region
        render_widths = ratio_resolve(width, children)
        offset = 0
        _Region = Region
        for child, child_width in zip(children, render_widths):
            yield child, _Region(x + offset, y, child_width, height)
            offset += child_width


class ColumnSplitter(Splitter):
    """Split a layout region in to columns."""

    name = "column"

    def get_tree_icon(self) -> str:
        return "[layout.tree.column]⬍"

    def divide(
        self, children: Sequence["Layout"], region: Region
    ) -> Iterable[Tuple["Layout", Region]]:
        x, y, width, height = region
        render_heights = ratio_resolve(height, children)
        offset = 0
        _Region = Region
        for child, child_height in zip(children, render_heights):
            yield child, _Region(x, y + offset, width, child_height)
            offset += child_height


@rich_repr
class Layout:
    """A renderable to divide a fixed height in to rows or columns.

    Args:
        renderable (RenderableType, optional): Renderable content, or None for placeholder. Defaults to None.
        name (str, optional): Optional identifier for Layout. Defaults to None.
        size (int, optional): Optional fixed size of layout. Defaults to None.
        minimum_size (int, optional): Minimum size of layout. Defaults to 1.
        ratio (int, optional): Optional ratio for flexible layout. Defaults to 1.
        visible (bool, optional): Visibility of layout. Defaults to True.
    """

    splitters = {"row": RowSplitter, "column": ColumnSplitter}

    def __init__(
        self,
        renderable: Optional[RenderableType] = None,
        *,
        name: Optional[str] = None,
        size: Optional[int] = None,
        minimum_size: int = 1,
        ratio: int = 1,
        visible: bool = True,
    ) -> None:
        self._renderable = renderable or _Placeholder(self)
        self.size = size
        self.minimum_size = minimum_size
        self.ratio = ratio
        self.name = name
        self.visible = visible
        self.splitter: Splitter = self.splitters["column"]()
        self._children: List[Layout] = []
        self._render_map: RenderMap = {}
        self._lock = RLock()
        self._render_cache: Dict[str, Any] = {}
        self._render_cache_order: "OrderedDict[str, None]" = OrderedDict()
        self._current_render_dims: Optional[Tuple[int, int]] = None
        self._current_hierarchy_path: Optional[str] = None
        self._last_render_key: Optional[str] = None
        self._last_render_dims: Optional[Tuple[int, int]] = None
        self._parent: Optional["Layout"] = None

    def __rich_repr__(self) -> Result:
        yield "name", self.name, None
        yield "size", self.size, None
        yield "minimum_size", self.minimum_size, 1
        yield "ratio", self.ratio, 1

    @property
    def renderable(self) -> RenderableType:
        """Layout renderable."""
        return self if self._children else self._renderable

    @property
    def children(self) -> List["Layout"]:
        """Gets (visible) layout children."""
        return [child for child in self._children if child.visible]

    @property
    def map(self) -> RenderMap:
        """Get a map of the last render."""
        return self._render_map

    def get(self, name: str) -> Optional["Layout"]:
        """Get a named layout, or None if it doesn't exist.

        Args:
            name (str): Name of layout.

        Returns:
            Optional[Layout]: Layout instance or None if no layout was found.
        """
        if self.name == name:
            return self
        else:
            for child in self._children:
                named_layout = child.get(name)
                if named_layout is not None:
                    return named_layout
        return None

    def __getitem__(self, name: str) -> "Layout":
        layout = self.get(name)
        if layout is None:
            raise KeyError(f"No layout with name {name!r}")
        return layout

    @property
    def tree(self) -> "Tree":
        """Get a tree renderable to show layout structure."""
        from rich.styled import Styled
        from rich.table import Table
        from rich.tree import Tree

        def summary(layout: "Layout") -> Table:
            icon = layout.splitter.get_tree_icon()

            table = Table.grid(padding=(0, 1, 0, 0))

            text: RenderableType = (
                Pretty(layout) if layout.visible else Styled(Pretty(layout), "dim")
            )
            table.add_row(icon, text)
            _summary = table
            return _summary

        layout = self
        tree = Tree(
            summary(layout),
            guide_style=f"layout.tree.{layout.splitter.name}",
            highlight=True,
        )

        def recurse(tree: "Tree", layout: "Layout") -> None:
            for child in layout._children:
                recurse(
                    tree.add(
                        summary(child),
                        guide_style=f"layout.tree.{child.splitter.name}",
                    ),
                    child,
                )

        recurse(tree, self)
        return tree

    def split(
        self,
        *layouts: Union["Layout", RenderableType],
        splitter: Union[Splitter, str] = "column",
    ) -> None:
        """Split the layout in to multiple sub-layouts.

        Args:
            *layouts (Layout): Positional arguments should be (sub) Layout instances.
            splitter (Union[Splitter, str]): Splitter instance or name of splitter.
        """
        _layouts = [
            layout if isinstance(layout, Layout) else Layout(layout)
            for layout in layouts
        ]
        try:
            self.splitter = (
                splitter
                if isinstance(splitter, Splitter)
                else self.splitters[splitter]()
            )
        except KeyError:
            raise NoSplitter(f"No splitter called {splitter!r}")
        for child in self._children:
            child._parent = None
        self._children[:] = _layouts
        for child in self._children:
            child._parent = self
        self.invalidate_cache()

    def add_split(self, *layouts: Union["Layout", RenderableType]) -> None:
        """Add a new layout(s) to existing split.

        Args:
            *layouts (Union[Layout, RenderableType]): Positional arguments should be renderables or (sub) Layout instances.

        """
        _layouts = [
            layout if isinstance(layout, Layout) else Layout(layout)
            for layout in layouts
        ]
        for layout in _layouts:
            layout._parent = self
        self._children.extend(_layouts)
        self.invalidate_cache()

    def split_row(self, *layouts: Union["Layout", RenderableType]) -> None:
        """Split the layout in to a row (layouts side by side).

        Args:
            *layouts (Layout): Positional arguments should be (sub) Layout instances.
        """
        self.split(*layouts, splitter="row")

    def split_column(self, *layouts: Union["Layout", RenderableType]) -> None:
        """Split the layout in to a column (layouts stacked on top of each other).

        Args:
            *layouts (Layout): Positional arguments should be (sub) Layout instances.
        """
        self.split(*layouts, splitter="column")

    def unsplit(self) -> None:
        """Reset splits to initial state."""
        for child in self._children:
            child._parent = None
        del self._children[:]
        self.invalidate_cache()

    def update(self, renderable: RenderableType) -> None:
        """Update renderable.

        Args:
            renderable (RenderableType): New renderable object.
        """
        with self._lock:
            self._renderable = renderable
            self.invalidate_cache()

    def refresh_screen(self, console: "Console", layout_name: str) -> None:
        """Refresh a sub-layout.

        Args:
            console (Console): Console instance where Layout is to be rendered.
            layout_name (str): Name of layout.
        """
        with self._lock:
            layout = self[layout_name]
            region, _lines = self._render_map[layout]
            (x, y, width, height) = region
            lines = console.render_lines(
                layout, console.options.update_dimensions(width, height)
            )
            self._render_map[layout] = LayoutRender(region, lines)
            console.update_screen_lines(lines, x, y)

    def _make_region_map(self, width: int, height: int) -> RegionMap:
        """Create a dict that maps layout on to Region."""
        stack: List[Tuple[Layout, Region]] = [(self, Region(0, 0, width, height))]
        push = stack.append
        pop = stack.pop
        layout_regions: List[Tuple[Layout, Region]] = []
        append_layout_region = layout_regions.append
        while stack:
            append_layout_region(pop())
            layout, region = layout_regions[-1]
            children = layout.children
            if children:
                for child_and_region in layout.splitter.divide(children, region):
                    push(child_and_region)

        region_map = {
            layout: region
            for layout, region in sorted(layout_regions, key=itemgetter(1))
        }
        return region_map

    def render(self, console: Console, options: ConsoleOptions) -> RenderMap:
        """Render the sub_layouts.

        Args:
            console (Console): Console instance.
            options (ConsoleOptions): Console options.

        Returns:
            RenderMap: A dict that maps Layout on to a tuple of Region, lines
        """
        render_width = options.max_width
        render_height = options.height or console.height

        with self._lock:
            self._current_render_dims = (render_width, render_height)
            self._current_hierarchy_path = "root"
            cache_key = self._generate_cache_key()
            cached_map = self._render_cache.get(cache_key)
            if isinstance(cached_map, dict):
                self._touch_cache_key(cache_key)
                self._render_map = cached_map  # type: ignore[assignment]
                self._last_render_key = cache_key
                self._last_render_dims = (render_width, render_height)
                self._current_render_dims = None
                self._current_hierarchy_path = None
                return cached_map  # type: ignore[return-value]

            region_map = self._make_region_map(render_width, render_height)
            layout_regions = [
                (layout, region)
                for layout, region in region_map.items()
                if not layout.children
            ]
            render_map: Dict["Layout", "LayoutRender"] = {}
            render_lines = console.render_lines
            update_dimensions = options.update_dimensions

            path_map = self._build_hierarchy_paths()

            for layout, region in layout_regions:
                with layout._lock:
                    layout._current_render_dims = (region.width, region.height)
                    layout._current_hierarchy_path = path_map.get(layout)
                    cached_lines = layout._get_cached_render()
                    if cached_lines is None:
                        lines = render_lines(
                            layout.renderable,
                            update_dimensions(region.width, region.height),
                        )
                        layout._store_cached_render(lines)
                        layout.evict_cache()
                    else:
                        lines = cached_lines
                    render_map[layout] = LayoutRender(region, lines)
                    layout._current_render_dims = None
                    layout._current_hierarchy_path = None

            self._render_map = render_map
            self._render_cache[cache_key] = render_map
            self._touch_cache_key(cache_key)
            self._last_render_key = cache_key
            self._last_render_dims = (render_width, render_height)
            self.evict_cache()
            self._current_render_dims = None
            self._current_hierarchy_path = None
            return render_map

    def cache_rendered_output(self) -> None:
        """Persist the most recent render map in the cache.

        Returns:
            None: This method is a no-op when no render data is available.
        """
        if self._last_render_key is None:
            return
        if self._last_render_key not in self._render_cache and self._render_map:
            self._render_cache[self._last_render_key] = self._render_map
        self._touch_cache_key(self._last_render_key)

    def invalidate_cache(self) -> None:
        """Invalidate cached renders for this layout and its ancestors.

        Returns:
            None: Cache structures are cleared in place.
        """
        with self._lock:
            self._invalidate_cache()
        self._invalidate_parent_caches()

    def evict_cache(self, max_entries: int = 100) -> None:
        """Remove least-recently-used cache entries beyond ``max_entries``.

        Args:
            max_entries (int): Maximum number of entries to retain.

        Returns:
            None
        """
        with self._lock:
            while len(self._render_cache_order) > max_entries:
                eldest, _ = self._render_cache_order.popitem(last=False)
                self._render_cache.pop(eldest, None)

    def _touch_cache_key(self, key: str) -> None:
        """Mark a cache key as most recently used.

        Args:
            key (str): Cache key to update in the LRU tracker.

        Returns:
            None
        """
        self._render_cache_order.pop(key, None)
        self._render_cache_order[key] = None

    def _store_cached_render(self, lines: List[List[Segment]]) -> None:
        """Store rendered line segments for the current context.

        Args:
            lines (List[List[Segment]]): Rendered segments grouped by line.

        Returns:
            None
        """
        key = self._generate_cache_key()
        self._render_cache[key] = lines
        self._touch_cache_key(key)
        self._last_render_key = key
        if self._current_render_dims is not None:
            self._last_render_dims = self._current_render_dims

    def _get_cached_render(self) -> Optional[List[List[Segment]]]:
        """Return cached line segments for the current render context.

        Returns:
            Optional[List[List[Segment]]]: Cached segments if present.
        """
        if self._current_render_dims is None:
            return None
        key = self._generate_cache_key()
        cached = self._render_cache.get(key)
        if isinstance(cached, list):
            self._touch_cache_key(key)
            self._last_render_key = key
            return cached
        return None

    def _invalidate_cache(self, recursive: bool = True) -> None:
        """Clear cached data for this layout.

        Args:
            recursive (bool): When ``True`` also clear descendant caches.

        Returns:
            None
        """
        self._render_cache.clear()
        self._render_cache_order.clear()
        self._last_render_key = None
        self._last_render_dims = None
        self._render_map = {}
        if recursive:
            for child in self._children:
                with child._lock:
                    child._invalidate_cache(recursive=True)

    def _invalidate_parent_caches(self) -> None:
        """Propagate invalidation to ancestor layouts without touching siblings.

        Returns:
            None
        """
        parent = self._parent
        if parent is None:
            return
        with parent._lock:
            parent._invalidate_cache(recursive=False)
        parent._invalidate_parent_caches()

    def _collect_style_signature(self, renderable: RenderableType) -> str:
        """Build a descriptive signature of layout and renderable styles.

        Args:
            renderable (RenderableType): Renderable associated with this layout.

        Returns:
            str: Stable textual summary of relevant style attributes.
        """
        style_bits: List[str] = []
        style_attrs = ("style", "border_style", "highlight", "box", "padding")
        for attr in style_attrs:
            if hasattr(self, attr):
                try:
                    value = getattr(self, attr)
                except Exception:
                    value = None
                if value is not None:
                    style_bits.append(f"layout.{attr}={value!r}")
        for attr in style_attrs:
            if hasattr(renderable, attr):
                try:
                    value = getattr(renderable, attr)
                except Exception:
                    value = None
                if value is not None:
                    style_bits.append(f"renderable.{attr}={value!r}")
        if not style_bits:
            return "none"
        return ",".join(sorted(style_bits))

    def _fingerprint_renderable(self, renderable: RenderableType) -> str:
        """Generate a stable fingerprint for the supplied renderable.

        Args:
            renderable (RenderableType): Renderable being inspected.

        Returns:
            str: Type-qualified digest suitable for cache keys.
        """
        state = self._normalise_renderable_state(renderable)
        try:
            payload = json.dumps(
                state, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            )
        except Exception:
            payload = repr(state)
        digest = hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()
        renderable_type = (
            f"{type(renderable).__module__}.{type(renderable).__qualname__}"
        )
        return f"{renderable_type}:{digest}"

    def _normalise_renderable_state(
        self,
        renderable: RenderableType,
        *,
        max_depth: int = 4,
    ) -> Any:
        """Convert a renderable into JSON-friendly primitives for hashing.

        Args:
            renderable (RenderableType): Renderable to normalise.
            max_depth (int): Maximum recursion depth for traversal.

        Returns:
            Any: JSON-serialisable representation of renderable state.
        """

        def _normalise(value: Any, depth: int, seen: Set[int]) -> Any:
            if isinstance(value, (str, int, float, bool)) or value is None:
                return value
            if isinstance(value, bytes):
                return value.decode("utf-8", "replace")
            if depth >= max_depth:
                return f"<depth:{type(value).__name__}>"

            value_id = id(value)
            if value_id in seen:
                return f"<cycle:{type(value).__name__}>"

            seen.add(value_id)
            try:
                if isinstance(value, dict):
                    return {
                        str(key): _normalise(item, depth + 1, seen)
                        for key, item in sorted(
                            value.items(), key=lambda item: str(item[0])
                        )
                    }
                if isinstance(value, (list, tuple)):
                    return [
                        _normalise(item, depth + 1, seen) for item in value
                    ]
                if isinstance(value, (set, frozenset)):
                    return [
                        _normalise(item, depth + 1, seen)
                        for item in sorted(value, key=lambda item: repr(item))
                    ]

                if hasattr(value, "__rich_repr__"):
                    try:
                        parts = list(value.__rich_repr__())
                    except Exception:
                        parts = []
                    else:
                        return [
                            _normalise(part, depth + 1, seen)
                            if not isinstance(part, tuple)
                            else [
                                _normalise(component, depth + 1, seen)
                                for component in part
                            ]
                            for part in parts
                        ]

                data: Dict[str, Any] = {}
                if hasattr(value, "__dict__"):
                    data.update(
                        {
                            str(key): _normalise(item, depth + 1, seen)
                            for key, item in value.__dict__.items()
                            if not callable(item)
                        }
                    )
                slots = getattr(value, "__slots__", ())
                if isinstance(slots, str):
                    slots = (slots,)
                for slot in slots:
                    try:
                        slot_value = getattr(value, slot)
                    except AttributeError:
                        continue
                    data[str(slot)] = _normalise(
                        slot_value, depth + 1, seen
                    )
                if data:
                    data["__type__"] = (
                        f"{type(value).__module__}.{type(value).__qualname__}"
                    )
                    return dict(sorted(data.items()))

                return repr(value)
            finally:
                seen.discard(value_id)

        return _normalise(renderable, 0, set())

    def _build_hierarchy_paths(self) -> Dict["Layout", str]:
        """Produce stable hierarchy paths for all descendants.

        Returns:
            Dict[Layout, str]: Mapping of layout nodes to their hierarchy path.
        """
        paths: Dict["Layout", str] = {self: "root"}

        def _recurse(node: "Layout", prefix: str) -> None:
            for index, child in enumerate(node._children):
                child_path = f"{prefix}/{index}"
                paths[child] = child_path
                _recurse(child, child_path)

        _recurse(self, "root")
        return paths

    def _generate_cache_key(self) -> str:
        """Generate a cache key capturing identity and render context.

        Returns:
            str: Deterministic cache key for the active render context.
        """
        width, height = self._current_render_dims or self._last_render_dims or (0, 0)
        path = self._current_hierarchy_path or self._build_hierarchy_path()
        structure = (
            f"name={self.name!r};size={self.size!r};min={self.minimum_size};"
            f"ratio={self.ratio};visible={self.visible}"
        )
        renderable = self._renderable if not self._children else "<container>"
        try:
            fingerprint = self._fingerprint_renderable(renderable)
        except Exception:
            fingerprint = f"type={type(renderable)!r}"
        style_signature = self._collect_style_signature(renderable)
        key = (
            f"path={path}|size={width}x{height}|struct={structure}|content={fingerprint}|style={style_signature}"
        )
        return key

    def _build_hierarchy_path(self) -> str:
        """Compute the path of this layout within its parent tree.

        Returns:
            str: Hierarchy path identifying the layout.
        """
        segments: List[str] = []
        node: Optional["Layout"] = self
        while node is not None:
            parent = node._parent
            if parent is None:
                segments.append("root")
                break
            try:
                index = parent._children.index(node)
            except ValueError:
                index = -1
            segments.append(str(index))
            node = parent
        return "/".join(reversed(segments))

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        with self._lock:
            width = options.max_width or console.width
            height = options.height or console.height
            render_map = self.render(console, options.update_dimensions(width, height))
            self._render_map = render_map
            layout_lines: List[List[Segment]] = [[] for _ in range(height)]
            _islice = islice
            for region, lines in render_map.values():
                _x, y, _layout_width, layout_height = region
                for row, line in zip(
                    _islice(layout_lines, y, y + layout_height), lines
                ):
                    row.extend(line)

            new_line = Segment.line()
            for layout_row in layout_lines:
                yield from layout_row
                yield new_line


if __name__ == "__main__":
    from rich.console import Console

    console = Console()
    layout = Layout()

    layout.split_column(
        Layout(name="header", size=3),
        Layout(ratio=1, name="main"),
        Layout(size=10, name="footer"),
    )

    layout["main"].split_row(Layout(name="side"), Layout(name="body", ratio=2))

    layout["body"].split_row(Layout(name="content", ratio=2), Layout(name="s2"))

    layout["s2"].split_column(
        Layout(name="top"), Layout(name="middle"), Layout(name="bottom")
    )

    layout["side"].split_column(Layout(layout.tree, name="left1"), Layout(name="left2"))

    layout["content"].update("foo")

    console.print(layout)
