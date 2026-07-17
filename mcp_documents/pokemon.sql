CREATE TABLE pokemon (
    pokedex_number INTEGER PRIMARY KEY,
    name VARCHAR(50),
    primary_type VARCHAR(20),
    secondary_type VARCHAR(20),
    hp INTEGER,
    attack INTEGER,
    defense INTEGER
);

INSERT INTO pokemon VALUES
(25, 'Pikachu', 'Electric', NULL, 35, 55, 40),
(6, 'Charizard', 'Fire', 'Flying', 78, 84, 78),
(143, 'Snorlax', 'Normal', NULL, 160, 110, 65),
(448, 'Lucario', 'Fighting', 'Steel', 70, 110, 70);