package com.minecorp.plugin;

import net.kyori.adventure.inventory.Book;
import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.event.ClickEvent;
import net.kyori.adventure.text.format.NamedTextColor;
import net.kyori.adventure.text.format.TextDecoration;

import java.util.ArrayList;
import java.util.List;

/** Turns an {@link ExplainBook} into an Adventure book: a link line opens its address, a jump line changes page. */
final class BookRender {

    private BookRender() {
    }

    static Book book(ExplainBook data) {
        List<Component> pages = new ArrayList<>();
        for (List<ExplainBook.Line> page : data.pages()) {
            pages.add(page(page));
        }
        return Book.book(Component.text(data.title()), Component.text(data.author()), pages);
    }

    static Component page(List<ExplainBook.Line> lines) {
        Component page = Component.empty();
        for (int i = 0; i < lines.size(); i++) {
            page = page.append(line(lines.get(i)));
            if (i < lines.size() - 1) {
                page = page.append(Component.newline());
            }
        }
        return page;
    }

    static Component line(ExplainBook.Line line) {
        Component text = Component.text(line.text());
        if (line.url() != null) {
            return text.color(NamedTextColor.BLUE).decorate(TextDecoration.UNDERLINED).clickEvent(ClickEvent.openUrl(line.url()));
        }
        if (line.jumpTo() != null) {
            return text.color(NamedTextColor.DARK_AQUA).clickEvent(ClickEvent.changePage(line.jumpTo()));
        }
        return text;
    }
}
