package com.minecorp.plugin;

import net.kyori.adventure.inventory.Book;
import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.event.ClickEvent;
import net.kyori.adventure.text.serializer.plain.PlainTextComponentSerializer;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

class BookRenderTest {

    private static final ExplainBook.Line PLAIN = new ExplainBook.Line("plain", null, null);
    private static final ExplainBook.Line LINK = new ExplainBook.Line("wiki", "https://minecraft.wiki/w/X", null);
    private static final ExplainBook.Line JUMP = new ExplainBook.Line("1. how?", null, 2);

    private static String plain(Component c) {
        return PlainTextComponentSerializer.plainText().serialize(c);
    }

    @Test
    void aLinkLineOpensItsAddress() {
        ClickEvent click = BookRender.line(LINK).clickEvent();
        assertEquals(ClickEvent.Action.OPEN_URL, click.action());
        assertEquals("https://minecraft.wiki/w/X", ((ClickEvent.Payload.Text) click.payload()).value());
    }

    @Test
    void aJumpLineChangesPage() {
        ClickEvent click = BookRender.line(JUMP).clickEvent();
        assertEquals(ClickEvent.Action.CHANGE_PAGE, click.action());
        assertEquals(2, ((ClickEvent.Payload.Int) click.payload()).integer());
    }

    @Test
    void aPlainLineHasNoClickEvent() {
        assertNull(BookRender.line(PLAIN).clickEvent());
    }

    @Test
    void aPageJoinsItsLinesWithNewlinesAndNoTrailingOne() {
        assertEquals("plain\n\nwiki", plain(BookRender.page(List.of(PLAIN, new ExplainBook.Line("", null, null), LINK))));
    }

    @Test
    void theBookHasTheTitleAuthorAndOnePagePerPage() {
        Book book = BookRender.book(new ExplainBook("Agent notes", "MineCorp Agent", List.of(List.of(PLAIN), List.of(LINK, JUMP))));
        assertEquals("Agent notes", plain(book.title()));
        assertEquals("MineCorp Agent", plain(book.author()));
        assertEquals(2, book.pages().size());
        assertEquals("wiki\n1. how?", plain(book.pages().get(1)));
    }
}
