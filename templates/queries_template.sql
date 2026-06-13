-- Prehistoric Fauna Library: Useful SQL Queries

-- Вывести все записи (Научная сортировка)
SELECT * FROM {table_species} 
ORDER BY genus ASC, is_type DESC, year ASC;

-- Поиск по конкретному роду
-- SELECT * FROM {table_species} WHERE genus = 'Tyrannosaurus';